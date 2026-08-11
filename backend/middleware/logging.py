"""Request logging middleware — records every HTTP request to the audit log."""

from __future__ import annotations

import time
from typing import Any, Callable


class RequestLoggingMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")
        started = time.perf_counter()
        status = 0

        async def send_wrapper(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            pipeline = getattr(getattr(self.app, "state", None), "pipeline", None)
            if pipeline is not None:
                try:
                    pipeline.audit.log(
                        "http.request",
                        action=f"{method} {path}",
                        actor="http",
                        decision={"status": status},
                        detail={"ms": duration_ms},
                    )
                except Exception:
                    pass
