# Emma AI Performance Optimizations

## Overview
This document summarizes the performance optimizations implemented in Emma. **No speedup below was benchmarked — every "x faster" figure is an engineering estimate, not a measurement.**

## Phase 1: Critical Optimizations (Implemented ✅)

### 1. HTTP Connection Pooling
**Files Modified:**
- `memory/embeddings.py` - Added pooled HTTP client with proper lifecycle
- `llm/local.py` - Added pooled HTTP client for LLM requests
- `capabilities/web_search.py` - Added pooled HTTP client for web searches

**Impact:** 10-50x speedup on HTTP operations by eliminating TCP handshake overhead per request

**Details:**
- Replaced `httpx.AsyncClient()` per-request creation with reusable pooled clients
- Configured keep-alive connections (max_keepalive_connections=5, max_connections=10)
- Added proper cleanup in lifespan handlers

### 2. SQLite Connection Pooling & Optimization
**Files Modified:**
- `memory/episodic.py` - Implemented thread-local connection pooling

**Impact:** 5-20x speedup on database operations

**Details:**
- Implemented thread-local SQLite connections with `check_same_thread=False`
- Enabled WAL mode for better concurrency
- Configured 64MB cache size
- Set synchronous=NORMAL for better performance
- Added proper connection lifecycle management

### 3. Response Caching
**Files Modified:**
- `memory/embeddings.py` - Added LRU cache for embeddings (1000 entry limit)
- `memory/episodic.py` - Added query result caching with 60s TTL
- `capabilities/web_search.py` - Added search result caching with 5min TTL
- `agents/router.py` - Added LRU cache for intent classification (1000 entry limit)

**Impact:** 50-100x speedup for repeated operations

**Details:**
- Embeddings cached with hash-based keys to save memory
- Query results cached with automatic TTL-based invalidation
- Search results cached with size limits (100 entries max)
- Intent classification cached for similar messages

### 4. Response Compression
**Files Modified:**
- `backend/main.py` - Added GZipMiddleware

**Impact:** 2-5x bandwidth reduction, faster transmission

**Details:**
- Compresses responses >1KB with gzip
- Reduces network latency for large JSON responses

### 5. Fast JSON Serialization
**Files Modified:**
- `backend/routers/chat.py` - Added orjson fallback for faster JSON

**Impact:** 2-3x faster JSON serialization in streaming

**Details:**
- Uses orjson when available (3x faster than standard json)
- Falls back to standard json if orjson not installed

### 6. Async File I/O
**Files Modified:**
- `capabilities/system_io.py` - Added aiofiles support for async file operations

**Impact:** 2-5x faster file operations, non-blocking

**Details:**
- Uses aiofiles for async file read/write when available
- Falls back to sync I/O if aiofiles not installed
- Prevents event loop blocking during file operations

### 7. Optimized Intent Classification
**Files Modified:**
- `agents/router.py` - Pre-compiled regex patterns, frozenset keyword lookups

**Impact:** 10-50ms reduction per request, O(1) keyword matching

**Details:**
- Pre-compiled regex patterns for panel commands
- Used frozenset for O(1) keyword lookups instead of O(n) lists
- Added LRU cache for classification results
- Optimized string operations

### 8. Efficient Similarity Scoring
**Files Modified:**
- `memory/episodic.py` - Early termination optimization in similarity search

**Impact:** 2-10x faster memory recall for large datasets

**Details:**
- Early termination when top k results found
- Maintains only top k+5 candidates to reduce memory
- Cached similarity results with TTL

### 9. Proper Resource Cleanup
**Files Modified:**
- `backend/main.py` - Added cleanup for HTTP clients, database connections
- `agents/router.py` - Added cleanup for all resources in close()
- `memory/embeddings.py` - Added close() method
- `llm/local.py` - Added close() method
- `capabilities/web_search.py` - Added close() method

**Impact:** Prevents memory leaks, ensures proper resource management

**Details:**
- All HTTP clients properly closed in lifespan
- Database connections cleaned up
- MQTT connections closed
- Browser automation resources released

### 10. Async HTTP Health Checks
**Files Modified:**
- `llm/local.py` - Made health checks synchronous for better performance
- `llm/router.py` - Simplified local availability check

**Impact:** Faster LLM routing decisions

**Details:**
- Health checks are now synchronous (non-blocking for routing)
- Cached availability with 10s TTL

## Phase 2: Additional Optimizations (Implemented ✅)

### 11. TTS Audio Caching (Extended)
**Files Modified:**
- `backend/tts_store.py` - Extended TTL from 120s to 3600s, added common response cache

**Impact:** 2-5x faster repeated TTS calls

**Details:**
- Extended TTL from 120s to 3600s (1 hour) for better caching
- Pre-cached common responses (yes, no, okay, etc.) with 2x TTL
- Hash-based segment IDs for common responses
- Reduced redundant TTS synthesis

### 12. Batch Embedding Support
**Files Modified:**
- `memory/embeddings.py` - Batch embedding method already existed
- `memory/episodic.py` - Added `remember_batch()` method for bulk operations

**Impact:** 5-10x faster bulk embedding operations

**Details:**
- Added `remember_batch()` method for efficient bulk memory storage
- Batch embedding reduces API calls by factor of N
- Supports both Supabase and SQLite batch operations
- Useful for importing historical data

### 13. Circuit Breaker for Supabase
**Files Modified:**
- `memory/supabase_client.py` - Implemented circuit breaker pattern

**Impact:** Prevents cascading failures, faster fallback to SQLite

**Details:**
- Circuit breaker opens after 3 consecutive failures
- 60-second timeout before attempting reconnection
- Health check caching with 30-second TTL
- Connection pooling with keep-alive
- Automatic failure tracking and recovery

### 14. Agent Pooling Infrastructure — REMOVED
`agents/agent_pool.py` was never referenced by any call path — dead code with
an unmeasured "2-5x faster" claim. It has been deleted. The tool-catalog /
system-prompt caching that did land lives in `agents/reasoning.py` (see #15).

### 15. Optimized Tool Catalog Caching
**Files Modified:**
- `agents/reasoning.py` - Cached tool catalog and system prompt

**Impact:** 1-2ms faster per planning operation

**Details:**
- Tool catalog computed once per agent instance
- System prompt generated once per agent instance
- Eliminates repeated JSON serialization
- Reduces memory allocations

## Phase 3: Advanced Optimizations (Implemented ✅)

### 16. Adaptive Caching System
**Files Modified:**
- `memory/adaptive_cache.py` - NEW: Adaptive cache with smart TTL and eviction
- `memory/embeddings.py` - Integrated adaptive cache for embeddings

**Impact:** 1.5-2x smarter cache management, better hit rates

**Details:**
- Adaptive TTL based on access patterns
- Multiple eviction policies (LRU, LFU, FIFO)
- Hit rate tracking for optimization
- Automatic cache size management
- TTL increases for frequently accessed items

### 17. Request Batching — Integrated into the Embedder
`orchestration/request_batcher.py` (single drain-worker batch queue) is wired
into `memory/embeddings.py`: concurrent `embed()` calls coalesce into one
Ollama `/api/embed` multi-input call, gated on the embedding model being
installed so the batch window is never paid when batching can't help. The
"2-5x faster" figure was never benchmarked — real impact depends on workload
concurrency.

### 18. Speculative Execution Framework — REMOVED
`orchestration/speculative_executor.py` was never referenced by any call path
(unbounded patterns dict, dead code) and its "2-3x faster" claim was never
measured. It has been deleted.

### 19. Performance Monitoring System
**Files Modified:**
- `performance/monitor.py` - NEW: Performance monitoring and metrics

**Impact:** Enables data-driven optimization decisions

**Details:**
- Performance metrics collection
- Latency tracking with percentiles
- Cache hit rate monitoring
- Resource usage tracking
- Decorator for automatic tracking
- Real-time statistics

### 20. Query Optimization Layer
**Status:** Infrastructure in place
**Recommendation:** Integrate query optimization with performance monitor
**Impact:** 2-5x faster database queries

### 21. Approximate Nearest Neighbor (ANN)
**Impact:** 10-100x faster similarity search for large datasets
**Status:** Not implemented (requires FAISS integration)

### 22. PGVector Index on Supabase
**Impact:** 5-50x faster vector similarity search
**Status:** Not implemented (requires Supabase pgvector setup)

## Dependencies Added

Added to `requirements.txt` and `pyproject.toml`:
- `aiofiles>=23.2.1` - Async file I/O
- `orjson>=3.9.10` - Fast JSON serialization

## Performance Estimates — NONE MEASURED

Every figure below is an unverified engineering estimate; no before/after
benchmark was run for any optimization in this document:

- HTTP Operations: 10-50x (connection pooling) — estimated
- Database Operations: 5-20x (pooling + optimization) — estimated
- Repeated Operations: 50-100x (caching) — estimated
- File Operations: 2-5x (async I/O) — estimated
- Intent Classification: 10-50ms faster (optimized patterns) — estimated
- Memory Recall: 2-10x (early termination + caching) — estimated
- Network Transfer: 2-5x (compression) — estimated
- JSON Serialization: 2-3x (orjson) — estimated
- TTS Synthesis: 2-5x (extended caching) — estimated
- Batch Embedding: 5-10x (batch operations) — estimated
- Supabase Operations: 2-5x (circuit breaker + health caching) — estimated

No combined figure is quoted: compounding unmeasured estimates into a
"200-1000x" headline would be fiction. Use the performance monitor
(`performance/monitor.py`, `/api/performance/stats`) for real numbers.

## Testing Recommendations

1. **Load Testing:** Test with concurrent requests to verify connection pooling
2. **Cache Hit Rate:** Monitor cache effectiveness with metrics
3. **Memory Usage:** Profile memory usage with large caches
4. **Latency Measurement:** Benchmark before/after for each optimization
5. **Error Handling:** Verify proper fallback when optimizations fail
6. **Circuit Breaker:** Test circuit breaker behavior with forced failures
7. **Batch Operations:** Benchmark batch vs individual operations

## Monitoring

Key metrics to monitor:
- HTTP client pool utilization
- Cache hit rates (embeddings, queries, searches)
- Database connection pool utilization
- Response times by operation type
- Memory usage over time
- Circuit breaker state and resets
- Batch operation efficiency

## Rollback Plan

If any optimization causes issues:
1. Remove or disable the specific optimization
2. Clear caches by restarting the service
3. Restore previous version from git if needed
4. Monitor system behavior after rollback

## Conclusion

The optimizations are implemented and the test suite passes. How much faster
Emma actually is remains **unmeasured** — every speedup in this document is an
estimate (see the Performance Estimates section). The dead-code modules (agent
pooling, speculative execution) were removed; the request batcher is integrated
into the embedder.
