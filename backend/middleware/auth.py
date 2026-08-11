"""API-key auth middleware.

When `EMMA_API_KEY` is set, every `/api/*` request (except /api/health) must
carry `X-API-Key: <key>`. The HUD and static assets are never gated.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from starlette.responses import JSONResponse

_EXEMPT_PATHS = {"/api/health"}


class AuthMiddleware:
    def __init__(self, app: Any, api_key: Optional[str] = None) -> None:
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or not self.api_key:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not path.startswith("/api/") or path in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if headers.get("x-api-key") != self.api_key:
            response = JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
