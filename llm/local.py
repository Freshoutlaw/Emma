"""Local LLM provider — Ollama on localhost:11434 (OpenAI-compatible style).

Uses Ollama's native `/api/chat` endpoint for generation and `/api/embeddings`
for embeddings. Works with any model Emma's operator has pulled, defaulting to
`qwen3:5.4b` per the project spec.

OPTIMIZATIONS:
- Connection pooling for HTTP clients
- Better timeout handling
- Streaming efficiency improvements
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

import httpx

from cost.usage import record_usage


class LLMUnavailable(RuntimeError):
    """Raised when no LLM provider is reachable (no Ollama, no Groq key)."""


class LocalLLM:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:5.4b",
        ping_timeout: float = 2.0,
        request_timeout: float = 300.0,
        chunk_timeout: float = 30.0,
        first_chunk_timeout: float = 120.0,
        num_ctx: Optional[int] = None,
        num_gpu: Optional[int] = None,
        keep_alive: Optional[int] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.ping_timeout = ping_timeout
        self.request_timeout = request_timeout
        # Per-chunk ceiling for streamed responses: a stalled Ollama surfaces
        # an error here instead of blocking a read for the full request_timeout.
        # The FIRST chunk gets its own larger allowance because a cold model
        # load happens before the first token (measured 11-15s on this box).
        self.chunk_timeout = chunk_timeout
        self.first_chunk_timeout = first_chunk_timeout
        # Ollama memory-footprint knobs — None means "let Ollama use its
        # server defaults" (num_ctx default is 4096, keep_alive 5m).  On
        # memory-constrained boxes these cap the KV cache and unload sooner.
        self.num_ctx = num_ctx
        self.num_gpu = num_gpu
        self.keep_alive = keep_alive
        # Use connection pooling for better performance
        self._client: Optional[httpx.Client] = None
        self._async_client: Optional[httpx.AsyncClient] = None
        
    def _get_client(self, timeout: Optional[float] = None) -> httpx.Client:
        """Get or create a pooled HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=timeout or self.request_timeout,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        return self._client

    def _get_async_client(self) -> httpx.AsyncClient:
        """Get or create a pooled ASYNC HTTP client."""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                timeout=self.request_timeout,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        return self._async_client

    # ---------------------------------------------------------------- health
    def available_models(self) -> list[str]:
        try:
            client = self._get_client(self.ping_timeout)
            response = client.get(f"{self.base_url}/api/tags")
            if response.status_code != 200:
                return []
            return [model.get("name", "") for model in response.json().get("models", [])]
        except Exception:
            return []

    def is_available(self) -> bool:
        return bool(self.available_models())

    # ---------------------------------------------------------------- chat
    def _payload(self, messages: list[dict], temperature: float, max_tokens: int, stream: bool) -> dict[str, Any]:
        options = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        # Memory-footprint knobs: only sent when configured, so the default
        # behavior (server defaults) is unchanged everywhere else.
        if self.num_ctx is not None:
            options["num_ctx"] = self.num_ctx
        if self.num_gpu is not None:
            options["num_gpu"] = self.num_gpu
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            # Reasoning models (qwen3, deepseek-r1) think by default, which
            # slows responses and can exhaust max_tokens before any answer is
            # produced. Turn the thinking pass off for direct, fast replies.
            "think": False,
            "options": options,
        }
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive
        return payload

    async def complete(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, model: Optional[str] = None) -> str:
        payload = self._payload(messages, temperature, max_tokens, stream=False)
        payload["model"] = model or self.model
        client = self._get_async_client()
        response = await client.post(f"{self.base_url}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        # Ollama surfaces failures (subscription/rate-limit/quota on :cloud
        # models, unknown models, …) as HTTP 200 with an "error" field.
        # Turn that into an exception so the router can fall back instead of
        # silently returning an empty reply.
        if data.get("error"):
            raise RuntimeError(f"Ollama error: {data['error']}")
        # cost dashboard — best-effort capture, never breaks the turn
        record_usage(data.get("model") or payload["model"], data)
        message = data.get("message") or {}
        content = message.get("content", "")
        if not content:
            # Older Ollama builds may ignore "think": false — surface the
            # thinking text rather than returning an empty reply.
            content = message.get("thinking", "")
        return content

    async def stream(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, model: Optional[str] = None) -> AsyncIterator[str]:
        """Stream using synchronous client wrapped in async generator."""
        import asyncio
        import threading
        
        payload = self._payload(messages, temperature, max_tokens, stream=True)
        payload["model"] = model or self.model
        client = self._get_client()
        
        def sync_stream():
            # The stream request carries its own read timeout (the larger
            # first-chunk allowance) so the executor thread unblocks in
            # bounded time when the server stalls, instead of waiting out
            # request_timeout (300s).  A cold model load counts against the
            # first read, hence the generous ceiling here.
            with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=httpx.Timeout(self.first_chunk_timeout),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = httpx.Response(200, content=line).json()
                    except Exception:
                        import json as _json
                        chunk = _json.loads(line)
                    # Ollama error bodies arrive as HTTP 200 with an "error"
                    # field (subscription/rate-limit/quota on :cloud models,
                    # unknown model, …).  Raise so the router can fall back
                    # to the local model instead of streaming nothing.
                    if chunk.get("error"):
                        raise RuntimeError(f"Ollama error: {chunk['error']}")
                    message = chunk.get("message") or {}
                    # Modern Ollama honors `think: false` and streams the
                    # model's reasoning into the `thinking` field with an
                    # EMPTY `content` until the answer starts.  Surfacing
                    # that reasoning as content (the old fallback for builds
                    # that ignored `think`) made gpt-oss cloud narrate its
                    # internal monologue aloud — so only stream real content.
                    content = message.get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        record_usage(chunk.get("model") or payload["model"], chunk)
                        break
        
        # Run synchronous generator in thread pool and yield results.
        # NB: StopIteration raised by next(gen) inside run_in_executor never
        # resolves the awaited future (asyncio quirk), so the old
        # `except StopIteration: break` never fired and every streamed
        # response hung at the end.  Catch it inside the worker and signal
        # completion with a sentinel instead.
        loop = asyncio.get_running_loop()
        gen = sync_stream()
        done = object()
        # Set when the async consumer stops iterating (error, timeout, or an
        # early aclose).  The worker thread checks it after each next(gen) so
        # the sync generator is closed by the thread that owns its execution —
        # closing a generator from another thread while next() is in flight is
        # unsafe, but close() from the same thread is not.
        abandoned = threading.Event()

        def next_chunk():
            try:
                chunk = next(gen)
            except StopIteration:
                return done
            # The async side gave up while this thread was inside next(gen)
            # (e.g. a per-chunk timeout).  Close the generator here so its
            # `with client.stream(...)` unwinds and the Ollama response /
            # pooled connection is released instead of lingering until GC.
            if abandoned.is_set():
                try:
                    gen.close()
                except Exception:
                    pass
                return done
            return chunk

        first_chunk = True
        try:
            while True:
                # Per-chunk deadline: steady-state stalls surface at chunk_timeout
                # (so a wedged Ollama falls back to cloud in ~30s), but the FIRST
                # chunk gets first_chunk_timeout because a cold model load happens
                # before the first token.  wait_for also guards against any cause
                # of a never-resolving future (the class of bug that used to hang
                # streaming at the end); the httpx read timeout above still bounds
                # the worker thread either way.
                timeout = self.first_chunk_timeout if first_chunk else self.chunk_timeout
                chunk = await asyncio.wait_for(
                    loop.run_in_executor(None, next_chunk),
                    timeout=timeout,
                )
                if chunk is done:
                    break
                first_chunk = False
                yield chunk
        finally:
            # Early exit (exception, timeout, or the caller closing us via
            # aclose): abandon the sync generator so its `with` block exits
            # and the HTTP connection returns to the pool promptly.  Skip the
            # close only if a worker thread is mid-next() — that thread will
            # close it via the abandoned flag above once it unblocks.
            abandoned.set()
            if not gen.gi_running:
                try:
                    gen.close()
                except Exception:
                    pass

    # ---------------------------------------------------------------- embeddings
    def embed(self, text: str, model: Optional[str] = None) -> list[float]:
        payload = {"model": model or self.model, "prompt": text[:8000]}
        client = self._get_client()
        response = client.post(f"{self.base_url}/api/embeddings", json=payload)
        response.raise_for_status()
        return response.json().get("embedding", [])
    
    async def close(self) -> None:
        """Clean up HTTP clients (sync + async)."""
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
