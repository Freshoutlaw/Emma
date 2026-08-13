"""Emma backend — FastAPI application entry point.

Run from the project root:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000

The app serves the HUD dashboard at `/` and the API under `/api/*`.

OPTIMIZATIONS:
- Response compression with gzip
- Proper resource cleanup in lifespan
- Connection pooling for all HTTP clients
- Domain-based LLM routing (localhost = qwen, onrender.com = cloud)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from agents.router import Pipeline
from backend.config import Settings
from backend.middleware.auth import AuthMiddleware
from backend.middleware.logging import RequestLoggingMiddleware
from backend.routers import chat, security, system, tts, voice, performance
from board.scheduler import BoardScheduler


def get_domain() -> str:
    """Detect the domain from environment or default to localhost."""
    # Check for environment variable
    domain = os.getenv("EMMA_DOMAIN")
    if domain:
        return domain
    
    # Check if running on Render
    if os.getenv("RENDER"):
        return "onrender.com"
    
    # Default to localhost
    return "localhost"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()
    
    # Detect domain and pass to pipeline
    domain = get_domain()
    settings.domain = domain
    
    pipeline = Pipeline(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pipeline.audit.log("app.started", actor="system", detail={"version": settings.version, "domain": domain})
        # Tier 7: the standing monthly review — the board shows up when you
        # didn't call the meeting. Runs on the always-on backend surface.
        board_scheduler = BoardScheduler(
            convene=pipeline.board_agent.convene,
            store=pipeline.board_agent.store,
        )
        app.state.board_scheduler = board_scheduler
        await board_scheduler.start()
        yield
        await board_scheduler.stop()
        # Proper resource cleanup - handled by pipeline.close()
        await pipeline.close()
        pipeline.audit.log("app.stopped", actor="system")

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Emma — a self-improving, autonomous AI assistant.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.pipeline = pipeline

    # Add response compression for better performance
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Middleware order: AuthMiddleware is added last, so it runs outermost.
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(AuthMiddleware, api_key=settings.api_key)

    app.include_router(chat.router)
    app.include_router(voice.router)
    app.include_router(tts.router)
    app.include_router(system.router)
    app.include_router(security.router)
    app.include_router(performance.router)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "name": settings.app_name, "version": settings.version, "domain": domain}

    # Serve the HUD last so API routes take precedence over the catch-all mount.
    hud_dir = settings.hud_dir
    if hud_dir.exists():
        app.mount("/", StaticFiles(directory=str(hud_dir), html=True), name="hud")

    return app


app = create_app()
