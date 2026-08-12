"""Performance router — performance metrics and monitoring endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from performance.turn_metrics import turn_metrics

router = APIRouter(prefix="/api/performance", tags=["performance"])


def _perf_monitor():
    """Get performance monitor instance."""
    try:
        from performance.monitor import perf_monitor
        return perf_monitor
    except ImportError:
        return None


def _get_embedder_stats(request: Request) -> dict:
    """Embedder batching + cache effectiveness from the running pipeline.

    The batcher stats make coalescing observable in the running app: after a
    wave of concurrent embeds, batches_completed stays low while
    requests_processed grows — the ratio is the average batch size.
    """
    try:
        pipeline = getattr(request.app.state, "pipeline", None)
        if pipeline is None or not hasattr(pipeline, "embedder"):
            return {"status": "no_embedder"}
        embedder = pipeline.embedder
        batcher = getattr(embedder, "_batcher", None)
        return {
            "status": "ok",
            "batcher": batcher.stats() if batcher is not None else None,
            "cache": embedder.get_cache_stats(),
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@router.get("/stats")
async def get_performance_stats(request: Request):
    """Get current performance statistics."""
    monitor = _perf_monitor()
    if monitor is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "Performance monitoring not available"}
        )
    
    summary = monitor.get_metrics_summary()
    if isinstance(summary, dict):
        summary["embedder"] = _get_embedder_stats(request)
        summary["turns"] = turn_metrics.snapshot()
    return summary


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
    turn_metrics.reset()
    return {"status": "cleared"}
