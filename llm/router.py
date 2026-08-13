"""Hybrid LLM router.

- LOCAL  (PC/localhost): Ollama on localhost:11434. When an Ollama Cloud
  model is configured (`ollama_cloud_model`, e.g. `gpt-oss:120b-cloud`) it is
  tried FIRST — the local Ollama binary proxies `:cloud` tags to ollama.com,
  so inference runs off-box.  If the cloud call fails (free-tier quota / rate
  limit / subscription gone), the locally-pulled model (e.g. `qwen3.5:2b`)
  is the fallback.
- CLOUD  (onrender.com): Groq API with `llama-3.3-70b-versatile`

The router auto-detects the environment by checking the domain. If running on
localhost/PC, it uses Ollama (cloud-first, local fallback). If running on
onrender.com, it uses Groq. Both providers speak OpenAI-compatible message
formats. Availability is cached for a few seconds so `complete()` doesn't
hammer the ping endpoint.
"""

from __future__ import annotations

import time
from typing import AsyncIterator, Optional

from llm.cloud import CloudLLM
from llm.local import LLMUnavailable, LocalLLM

from orchestration.failure_isolation import failure_isolation
from performance.turn_metrics import turn_metrics

# Circuit-breaker key for the Ollama Cloud provider.  Repeated cloud failures
# (exhausted quota, subscription, rate limit) open this circuit; while OPEN
# the cloud attempt is skipped so the local fallback answers without paying
# a doomed round trip on every turn.
OLLAMA_CLOUD_CIRCUIT_KEY = "ollama_cloud"


class LLMRouter:
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        groq_api_key: Optional[str] = None,
        local_model: str = "qwen3:5.4b",
        cloud_model: str = "llama-3.3-70b-versatile",
        ollama_cloud_model: Optional[str] = None,
        domain: str = "localhost",
        num_ctx: Optional[int] = None,
        num_gpu: Optional[int] = None,
        keep_alive: Optional[int] = None,
    ) -> None:
        self.ollama_url = ollama_url
        self.local_model = local_model
        self.cloud_model = cloud_model
        self.ollama_cloud_model = ollama_cloud_model
        self.domain = domain
        # Ollama memory-footprint knobs, forwarded to LocalLLM (the LOCAL
        # fallback model).  None = let Ollama use its server defaults.
        self.num_ctx = num_ctx
        self.num_gpu = num_gpu
        self.keep_alive = keep_alive
        self.local = LocalLLM(
            ollama_url,
            local_model,
            num_ctx=num_ctx,
            num_gpu=num_gpu,
            keep_alive=keep_alive,
        )
        # Ollama Cloud primary (e.g. "gpt-oss:120b-cloud") — the local Ollama
        # binary proxies :cloud tags to ollama.com.  Tried FIRST; the local
        # model above takes over when the cloud quota is exhausted.  None = no
        # cloud-first, local-only (the old behavior).  No memory knobs are
        # forwarded — they only make sense for models running on this box.
        self.ollama_cloud = LocalLLM(ollama_url, ollama_cloud_model) if ollama_cloud_model else None
        self.cloud = CloudLLM(groq_api_key, cloud_model)
        self._avail_cache: Optional[tuple[float, bool]] = None
        # Which provider/model actually served the last successful call — set
        # by complete()/stream() and surfaced per-turn (e.g. so the HUD can
        # show "[via gemma4:31b-cloud]" vs "[via local qwen3.5:2b fallback]").
        self.last_served: Optional[dict] = None

    # ---------------------------------------------------------------- detect
    def is_local_available(self) -> bool:
        now = time.monotonic()
        if self._avail_cache and now - self._avail_cache[0] < 10:
            return self._avail_cache[1]
        ok = self.local.is_available()
        self._avail_cache = (now, ok)
        return ok

    def route(self) -> str:
        """Return 'local' or 'cloud' based on domain."""
        # Local PC uses qwen, onrender.com uses cloud
        if self.domain == "localhost" or self.domain == "127.0.0.1":
            return "local"
        if "onrender.com" in self.domain:
            return "cloud"
        # Fallback to availability check
        if self.is_local_available():
            return "local"
        if self.cloud.is_available():
            return "cloud"
        return "none"

    def resolve_local_model(self) -> Optional[str]:
        """The configured model if pulled locally, else the first available model.

        Lets Emma run out of the box on machines where only a differently-named
        model is present (e.g. qwen3:4b instead of qwen3:5.4b).
        """
        available = self.local.available_models()
        if not available:
            return None
        if self.local_model in available:
            return self.local_model
        return available[0]

    def model(self) -> Optional[str]:
        route = self.route()
        if route == "local":
            if self.ollama_cloud is not None:
                return self.ollama_cloud.model
            return self.resolve_local_model() or self.local_model
        if route == "cloud":
            return self.cloud_model
        return None

    # ---------------------------------------------------------------- chat
    async def complete(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> str:
        """Complete a chat (OpenAI-style message list) with the best provider.

        `model` binds the call to ONE specific Ollama model (sub-agent LLMs):
        that model serves directly, and on failure the call falls back to the
        normal routing (cloud gemma4 primary, local, Groq).

        Ollama path (localhost): the Ollama Cloud model is tried FIRST; when
        it fails (subscription expired, free-tier quota/rate limit, timeout)
        the local model takes over as fallback.  Failures of both fall through
        to Groq; LLMUnavailable is only raised when every provider fails.
        Routing counters feed /api/performance so cloud-vs-local is observable.
        """
        if model:
            # Per-agent model binding — the sub-agent's own Ollama model.
            try:
                result = await self.local.complete(messages, temperature, max_tokens, model=model)
                self.last_served = {"provider": "agent", "model": model}
                turn_metrics.record_llm_call("local", ok=True)
                return result
            except Exception:
                turn_metrics.record_llm_call("local", ok=False)
        route = self.route()
        if route == "local":
            # Cloud-first via the Ollama proxy — skipped while the circuit is
            # OPEN so an exhausted cloud quota doesn't add latency to every turn.
            if self.ollama_cloud is not None:
                allowed, _ = failure_isolation.can_call(OLLAMA_CLOUD_CIRCUIT_KEY)
                if not allowed:
                    failure_isolation.reject(OLLAMA_CLOUD_CIRCUIT_KEY)
                else:
                    try:
                        result = await self.ollama_cloud.complete(messages, temperature, max_tokens)
                        failure_isolation.record_success(OLLAMA_CLOUD_CIRCUIT_KEY)
                        self.last_served = {"provider": "cloud", "model": self.ollama_cloud.model}
                        turn_metrics.record_llm_call("cloud", ok=True)
                        return result
                    except Exception:
                        failure_isolation.record_failure(OLLAMA_CLOUD_CIRCUIT_KEY)
                        turn_metrics.record_llm_call("cloud", ok=False)
            # Local fallback.
            try:
                local_model_name = self.resolve_local_model()
                result = await self.local.complete(
                    messages, temperature, max_tokens, model=local_model_name
                )
                self.last_served = {"provider": "local", "model": local_model_name}
                turn_metrics.record_llm_call("local", ok=True)
                return result
            except Exception:
                turn_metrics.record_llm_call("local", ok=False)
        if route == "cloud" or self.cloud.is_available():
            result = await self.cloud.complete(messages, temperature, max_tokens)
            self.last_served = {"provider": "groq", "model": self.cloud_model}
            turn_metrics.record_llm_call("cloud", ok=True)
            return result
        raise LLMUnavailable(
            "No LLM provider available — Ollama cloud/local models unavailable "
            "(check ollama.com quota) or set GROQ_API_KEY."
        )
    async def stream(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream a chat completion token-by-token from the best provider.

        `model` binds the stream to ONE specific Ollama model (sub-agent
        LLMs).  Mid-stream failures NEVER fall back (the duplicate tokens
        rule below applies to the bound model too).

        Ollama path (localhost): the Ollama Cloud model is tried first and the
        local model is the fallback when the cloud quota is exhausted.  A
        mid-stream failure NEVER falls back (the duplicate tokens rule below);
        quota errors surface before the first token, so the fallback works.
        """
        if model:
            emitted = False
            try:
                async for token in self.local.stream(messages, temperature, max_tokens, model=model):
                    emitted = True
                    yield token
                self.last_served = {"provider": "agent", "model": model}
                turn_metrics.record_llm_call("local", ok=True)
                return
            except Exception:
                turn_metrics.record_llm_call("local", ok=False)
                if emitted:
                    raise  # duplicate tokens rule — never restart mid-stream
        route = self.route()
        if route == "local":
            if self.ollama_cloud is not None:
                allowed, _ = failure_isolation.can_call(OLLAMA_CLOUD_CIRCUIT_KEY)
                if not allowed:
                    # Circuit OPEN — skip the doomed cloud round trip.
                    failure_isolation.reject(OLLAMA_CLOUD_CIRCUIT_KEY)
                else:
                    emitted = False
                    try:
                        async for token in self.ollama_cloud.stream(messages, temperature, max_tokens):
                            emitted = True
                            yield token
                        failure_isolation.record_success(OLLAMA_CLOUD_CIRCUIT_KEY)
                        self.last_served = {"provider": "cloud", "model": self.ollama_cloud.model}
                        turn_metrics.record_llm_call("cloud", ok=True)
                        return
                    except Exception:
                        failure_isolation.record_failure(OLLAMA_CLOUD_CIRCUIT_KEY)
                        turn_metrics.record_llm_call("cloud", ok=False)
                        # If any token already reached the caller, falling back to
                        # local would re-emit it and duplicate the response.  Fail
                        # the stream instead.  A failure before the first token
                        # sent nothing yet, so it is safe to fall through.
                        if emitted:
                            raise
            emitted = False
            try:
                local_model_name = self.resolve_local_model()
                async for token in self.local.stream(
                    messages, temperature, max_tokens, model=local_model_name
                ):
                    emitted = True
                    yield token
                self.last_served = {"provider": "local", "model": local_model_name}
                turn_metrics.record_llm_call("local", ok=True)
                return
            except Exception:
                turn_metrics.record_llm_call("local", ok=False)
                # Same duplicate-token rule: only fall through to Groq if the
                # local model failed before emitting anything.
                if emitted:
                    raise
        if route == "cloud" or self.cloud.is_available():
            async for token in self.cloud.stream(messages, temperature, max_tokens):
                yield token
            self.last_served = {"provider": "groq", "model": self.cloud_model}
            turn_metrics.record_llm_call("cloud", ok=True)
            return
        raise LLMUnavailable(
            "No LLM provider available — Ollama cloud/local models unavailable "
            "(check ollama.com quota) or set GROQ_API_KEY."
        )
