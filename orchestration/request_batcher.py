"""Request Batcher — coalesce similar async operations into batch calls.

OPTIMIZATIONS:
- Batch similar operations together
- Reduce API call overhead
- Automatic batch size optimization

Design
------
A single background worker task drains the queue in windows.  When the first
request arrives the worker waits up to ``max_wait_time`` for siblings (or
until the batch is full), then calls ``batch_processor`` once with all the
payloads and resolves each caller's future with the matching result.

Why a single worker?  The previous design spawned a per-submit task racing a
boolean ``_processing`` flag; a submit that arrived while a batch was in
flight hit an early-return and nothing ever drained it — its future hung
forever.  The worker loops until the queue is empty, so requests submitted
mid-batch are picked up by the next window and no future is ever stranded.

Contract
--------
``batch_processor(payloads: list[Any]) -> list[Any]`` must return exactly one
result per payload, in the same order.  A count mismatch or an exception
fails every future in the batch (never hangs).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional


@dataclass
class BatchRequest:
    id: str
    data: Any
    timestamp: float


class RequestBatcher:
    """Coalesce concurrent ``submit()`` calls into one ``batch_processor`` call."""

    def __init__(
        self,
        batch_processor: Callable[[List[Any]], List[Any]],
        max_batch_size: int = 10,
        max_wait_time: float = 0.05,
        min_batch_size: int = 1,
    ) -> None:
        self.batch_processor = batch_processor
        self.max_batch_size = max_batch_size
        self.max_wait_time = max_wait_time
        # Retained for API compatibility.  With the single-worker design the
        # queue is drained at the window deadline regardless of size, so no
        # request is ever stranded (values > 1 are effectively treated as 1).
        self.min_batch_size = min_batch_size
        self._queue: Deque[BatchRequest] = deque()
        self._pending: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._wake: Optional[asyncio.Event] = None
        self._worker: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._closed = False
        self._next_id = 0
        self._batches_completed = 0
        self._requests_processed = 0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Spawn the background drain worker (idempotent)."""
        if self._worker is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._worker = asyncio.create_task(self._drain_loop())

    async def close(self) -> None:
        """Stop the worker and fail any outstanding futures."""
        self._closed = True
        if self._wake is not None:
            self._wake.set()
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        async with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
            self._queue.clear()
        for future in pending:
            if not future.done():
                future.set_exception(RuntimeError("RequestBatcher closed"))

    # ------------------------------------------------------------------ api
    async def submit(self, data: Any) -> Any:
        """Enqueue ``data`` and return the result from the next batch."""
        self.start()
        self._next_id += 1
        request_id = f"req_{self._next_id}"
        assert self._loop is not None
        future = self._loop.create_future()
        async with self._lock:
            if self._closed:
                raise RuntimeError("RequestBatcher closed")
            self._queue.append(
                BatchRequest(id=request_id, data=data, timestamp=time.monotonic())
            )
            self._pending[request_id] = future
            # Set the wake under the same lock as the emptiness check in the
            # worker, so a submit can never slip between a clear and a wait.
            assert self._wake is not None
            self._wake.set()
        return await future

    # ------------------------------------------------------------------ worker
    async def _drain_loop(self) -> None:
        while True:
            # Wait for the queue to become non-empty (or shutdown).
            async with self._lock:
                if self._closed and not self._queue:
                    return
                if self._queue:
                    has_work = True
                else:
                    has_work = False
                    assert self._wake is not None
                    self._wake.clear()
            if not has_work:
                assert self._wake is not None
                await self._wake.wait()

            # Batch window: give concurrent submits a chance to join.
            if self.max_wait_time > 0:
                assert self._loop is not None
                deadline = self._loop.time() + self.max_wait_time
                while True:
                    async with self._lock:
                        qlen = len(self._queue)
                    if qlen >= self.max_batch_size:
                        break
                    remaining = deadline - self._loop.time()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(0.005, remaining))

            # Drain one batch.
            async with self._lock:
                if not self._queue:
                    continue
                batch_size = min(len(self._queue), self.max_batch_size)
                batch = [self._queue.popleft() for _ in range(batch_size)]

            try:
                payloads = [req.data for req in batch]
                result = self.batch_processor(payloads)
                if inspect.isawaitable(result):
                    result = await result
                results = result
                if len(results) != len(batch):
                    raise RuntimeError(
                        f"batch processor returned {len(results)} results for {len(batch)} requests"
                    )
            except Exception as exc:
                for req in batch:
                    self._resolve(req.id, exc=exc)
            else:
                for req, result in zip(batch, results):
                    self._resolve(req.id, result=result)
                async with self._lock:
                    self._batches_completed += 1
                    self._requests_processed += len(batch)

    def _resolve(
        self,
        request_id: str,
        *,
        result: Any = None,
        exc: Optional[BaseException] = None,
    ) -> None:
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return
        if exc is not None:
            future.set_exception(exc)
        else:
            future.set_result(result)

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        return {
            "queue_size": len(self._queue),
            "pending_futures": len(self._pending),
            "max_batch_size": self.max_batch_size,
            "max_wait_time": self.max_wait_time,
            "batches_completed": self._batches_completed,
            "requests_processed": self._requests_processed,
        }
