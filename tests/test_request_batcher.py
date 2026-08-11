"""Tests for the RequestBatcher — coalescing, ordering, and liveness.

The liveness tests matter: the previous design (a per-submit task racing a
boolean flag) could strand futures forever when a submit arrived while a
batch was being processed.  The worker model must never strand a request.
"""

import asyncio

from orchestration.request_batcher import RequestBatcher


def test_batches_concurrent_submits_into_one_call():
    calls = []

    def processor(payloads):
        calls.append(list(payloads))
        return [f"r{i}" for i in range(len(payloads))]

    async def run():
        batcher = RequestBatcher(processor, max_batch_size=10, max_wait_time=0.2)
        try:
            results = await asyncio.gather(*(batcher.submit(i) for i in range(5)))
            return results
        finally:
            await batcher.close()

    results = asyncio.run(run())
    assert results == ["r0", "r1", "r2", "r3", "r4"]
    assert calls == [[0, 1, 2, 3, 4]]


def test_order_preserved_across_batches():
    calls = []

    async def processor(payloads):
        calls.append(list(payloads))
        return list(payloads)

    async def run():
        batcher = RequestBatcher(processor, max_batch_size=10, max_wait_time=0.05)
        try:
            first = await asyncio.gather(*(batcher.submit(x) for x in ["a1", "a2"]))
            # Wait well past the first window so the second wave forms its own batch.
            await asyncio.sleep(0.15)
            second = await asyncio.gather(*(batcher.submit(x) for x in ["b1", "b2"]))
            return first, second
        finally:
            await batcher.close()

    first, second = asyncio.run(run())
    assert first == ["a1", "a2"]
    assert second == ["b1", "b2"]
    assert calls == [["a1", "a2"], ["b1", "b2"]]


def test_submit_during_inflight_batch_is_not_stranded():
    """Regression: requests submitted while a batch is being processed must
    still be drained and resolved (the old code stranded them forever)."""
    processed = []

    async def processor(payloads):
        processed.append(list(payloads))
        await asyncio.sleep(0.1)  # slow processor -> second wave lands mid-flight
        return list(payloads)

    async def run():
        batcher = RequestBatcher(processor, max_batch_size=10, max_wait_time=0.05)
        try:
            wave1 = [asyncio.ensure_future(batcher.submit(i)) for i in range(3)]
            await asyncio.sleep(0.12)  # let the first batch start processing
            wave2 = [asyncio.ensure_future(batcher.submit(i)) for i in range(3, 5)]
            results = await asyncio.wait_for(asyncio.gather(*(wave1 + wave2)), timeout=5)
            return results
        finally:
            await batcher.close()

    results = asyncio.run(run())
    assert results == [0, 1, 2, 3, 4]
    assert processed == [[0, 1, 2], [3, 4]]


def test_processor_error_rejects_batch_futures():
    async def processor(payloads):
        raise ValueError("boom")

    async def run():
        batcher = RequestBatcher(processor, max_batch_size=10, max_wait_time=0.05)
        try:
            await batcher.submit("x")
        finally:
            await batcher.close()

    try:
        asyncio.run(run())
        raised = False
    except ValueError:
        raised = True
    assert raised is True


def test_result_count_mismatch_rejects_instead_of_hanging():
    async def processor(payloads):
        return ["only_one"]

    async def run():
        batcher = RequestBatcher(processor, max_batch_size=10, max_wait_time=0.05)
        try:
            await asyncio.gather(batcher.submit("a"), batcher.submit("b"))
        finally:
            await batcher.close()

    try:
        asyncio.run(run())
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "returned 1 results for 2" in str(exc)
    assert raised is True


def test_single_submit_resolves_after_window():
    async def processor(payloads):
        return [f"v:{p}" for p in payloads]

    async def run():
        batcher = RequestBatcher(processor, max_batch_size=10, max_wait_time=0.05)
        try:
            return await batcher.submit("solo")
        finally:
            await batcher.close()

    assert asyncio.run(run()) == "v:solo"


def test_batch_size_cap_splits_batches():
    calls = []

    async def processor(payloads):
        calls.append(len(payloads))
        return list(payloads)

    async def run():
        batcher = RequestBatcher(processor, max_batch_size=10, max_wait_time=0.2)
        try:
            return await asyncio.gather(*(batcher.submit(i) for i in range(12)))
        finally:
            await batcher.close()

    results = asyncio.run(run())
    assert results == list(range(12))
    assert calls == [10, 2]


def test_close_rejects_pending_futures():
    async def processor(payloads):
        await asyncio.sleep(1.0)  # hold the batch open so close() must reject
        return list(payloads)

    async def run():
        batcher = RequestBatcher(processor, max_batch_size=10, max_wait_time=0.05)
        task = asyncio.ensure_future(batcher.submit("x"))
        await asyncio.sleep(0.12)  # let it enter the batch
        await batcher.close()
        try:
            await asyncio.wait_for(task, timeout=2)
        except RuntimeError as exc:
            return str(exc)
        return None

    message = asyncio.run(run())
    assert message is not None and "closed" in message


def test_submit_after_close_raises():
    async def processor(payloads):
        return list(payloads)

    async def run():
        batcher = RequestBatcher(processor, max_wait_time=0.01)
        await batcher.close()
        await batcher.submit("x")

    try:
        asyncio.run(run())
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "closed" in str(exc)
    assert raised is True
