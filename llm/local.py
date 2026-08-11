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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.ping_timeout = ping_timeout
        self.request_timeout = request_timeout
        # Use connection pooling for better performance
        self._client: Optional[httpx.Client] = None
        
    def _get_client(self, timeout: Optional[float] = None) -> httpx.Client:
        """Get or create a pooled HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=timeout or self.request_timeout,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        return self._client

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
        return {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            # Reasoning models (qwen3, deepseek-r1) think by default, which
            # slows responses and can exhaust max_tokens before any answer is
            # produced. Turn the thinking pass off for direct, fast replies.
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

    def complete(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, model: Optional[str] = None) -> str:
        payload = self._payload(messages, temperature, max_tokens, stream=False)
        payload["model"] = model or self.model
        client = self._get_client()
        response = client.post(f"{self.base_url}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
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
        
        payload = self._payload(messages, temperature, max_tokens, stream=True)
        payload["model"] = model or self.model
        client = self._get_client()
        
        def sync_stream():
            with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = httpx.Response(200, content=line).json()
                    except Exception:
                        import json as _json
                        chunk = _json.loads(line)
                    message = chunk.get("message") or {}
                    content = message.get("content", "")
                    if not content:
                        content = message.get("thinking", "")
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

        def next_chunk():
            try:
                return next(gen)
            except StopIteration:
                return done

        while True:
            chunk = await loop.run_in_executor(None, next_chunk)
            if chunk is done:
                break
            yield chunk

    # ---------------------------------------------------------------- embeddings
    def embed(self, text: str, model: Optional[str] = None) -> list[float]:
        payload = {"model": model or self.model, "prompt": text[:8000]}
        client = self._get_client()
        response = client.post(f"{self.base_url}/api/embeddings", json=payload)
        response.raise_for_status()
        return response.json().get("embedding", [])
    
    def close(self) -> None:
        """Clean up HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None
