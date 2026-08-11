"""Hybrid LLM router.

- LOCAL  (PC/localhost): Ollama running `qwen3:5.4b` on localhost:11434
- CLOUD  (onrender.com): Groq API with `llama-3.3-70b-versatile`

The router auto-detects the environment by checking the domain. If running on
localhost/PC, it uses Ollama (qwen). If running on onrender.com, it uses Groq.
Both providers speak OpenAI-compatible message formats. Availability is cached for
a few seconds so `complete()` doesn't hammer the ping endpoint.
"""

from __future__ import annotations

import time
from typing import AsyncIterator, Optional

from llm.cloud import CloudLLM
from llm.local import LLMUnavailable, LocalLLM


class LLMRouter:
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        groq_api_key: Optional[str] = None,
        local_model: str = "qwen3:5.4b",
        cloud_model: str = "llama-3.3-70b-versatile",
        domain: str = "localhost",
    ) -> None:
        self.ollama_url = ollama_url
        self.local_model = local_model
        self.cloud_model = cloud_model
        self.domain = domain
        self.local = LocalLLM(ollama_url, local_model)
        self.cloud = CloudLLM(groq_api_key, cloud_model)
        self._avail_cache: Optional[tuple[float, bool]] = None

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
            return self.resolve_local_model() or self.local_model
        if route == "cloud":
            return self.cloud_model
        return None

    # ---------------------------------------------------------------- chat
    async def complete(self, messages: list, temperature: float = 0.7, max_tokens: int = 4096) -> str:
        """Complete a chat (OpenAI-style message list) with the best provider.

        Local failures (missing model, timeout, bad response) fall through to
        the cloud provider; LLMUnavailable is only raised when both fail.
        """
        route = self.route()
        if route == "local":
            try:
                return await self.local.complete(
                    messages, temperature, max_tokens, model=self.resolve_local_model()
                )
            except Exception:
                pass  # fall through to cloud
        if route == "cloud" or self.cloud.is_available():
            return await self.cloud.complete(messages, temperature, max_tokens)
        raise LLMUnavailable(
            "No LLM provider available — start Ollama (localhost:11434) or set GROQ_API_KEY."
        )

    async def stream(self, messages: list, temperature: float = 0.7, max_tokens: int = 4096) -> AsyncIterator[str]:
        """Stream a chat completion token-by-token from the best provider."""
        route = self.route()
        if route == "local":
            emitted = False
            try:
                async for token in self.local.stream(
                    messages, temperature, max_tokens, model=self.resolve_local_model()
                ):
                    emitted = True
                    yield token
                return
            except Exception:
                # If any token already reached the caller, falling back to
                # cloud would re-emit it and duplicate the response.  Fail
                # the stream instead so the caller surfaces an error.  A
                # failure before the first token sent nothing yet, so it is
                # safe to fall through to cloud.
                if emitted:
                    raise
        if route == "cloud" or self.cloud.is_available():
            async for token in self.cloud.stream(messages, temperature, max_tokens):
                yield token
            return
        raise LLMUnavailable(
            "No LLM provider available — start Ollama (localhost:11434) or set GROQ_API_KEY."
        )
