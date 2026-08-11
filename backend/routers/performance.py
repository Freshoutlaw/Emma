"""Performance router — performance metrics and monitoring endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/performance", tags=["performance"])


def _perf_monitor():
    """Get performance monitor instance."""
    try:
        from performance.monitor import perf_monitor
        return perf_monitor
    except ImportError:
        return None


def _get_embedder_stats():
    """Get embedder cache statistics."""
    try:
        from agents.router import Pipeline
        # This would need to be passed in via request state
        return {"status": "available"}
    except ImportError:
        return {"status": "not_available"}


@router.get("/stats")
async def get_performance_stats(request: Request):
    """Get current performance statistics."""
    monitor = _perf_monitor()
    if monitor is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "Performance monitoring not available"}
        )
    
    return monitor.get_metrics_summary()


@router.get("/latency/{operation}")
async def get_latency_stats(operation: str, request: Request):
    """Get latency statistics for a specific operation."""
    monitor = _perf_monitor()
    if monitor is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "Performance monitoring not available"}
        )
    
    return monitor.get_latency_stats(operation)


@router.get("/latency")
async def get_all_latency_stats(request: Request):
    """Get latency statistics for all operations."""
    monitor = _perf_monitor()
    if monitor is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "Performance monitoring not available"}
        )
    
    return monitor.get_latency_stats()


@router.get("/cache/embeddings")
async def get_embedding_cache_stats(request: Request):
    """Get embedding cache statistics."""
    try:
        pipeline = request.app.state.pipeline
        if hasattr(pipeline, 'embedder'):
            return pipeline.embedder.get_cache_stats()
        return {"status": "no_embedder"}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error getting cache stats: {str(e)}"}
        )


@router.post("/clear")
async def clear_metrics(request: Request):
    """Clear all performance metrics."""
    monitor = _perf_monitor()
    if monitor is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "Performance monitoring not available"}
        )
    
    monitor.clear()
    return {"status": "cleared"}
