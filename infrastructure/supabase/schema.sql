-- ============================================================================
-- Emma — Supabase schema (episodic memory + pgvector RAG)
-- ============================================================================
-- Run this once in the Supabase SQL Editor (Dashboard → SQL Editor → New query),
-- or via psql as the postgres role. It is idempotent: safe to re-run.
--
-- After applying, Emma's episodic memory writes to `episodes` (Supabase primary,
-- SQLite fallback) and RAG recall uses the `match_episodes` pgvector RPC.
-- ============================================================================

-- pgvector extension for vector(384) embeddings (nomic-embed-text).
create extension if not exists vector;

-- Episodic memory rows.  `embedding` accepts pgvector's text form, which is
-- exactly what Emma sends (a JSON array string like "[0.1, 0.2, ...]").
create table if not exists public.episodes (
    id        text primary key,
    ts        timestamptz not null default now(),
    kind      text not null default 'episode',
    content   text not null,
    payload   text,
    embedding vector(384)
);

-- HNSW index for fast cosine-similarity search.
create index if not exists episodes_embedding_idx
    on public.episodes
    using hnsw (embedding vector_cosine_ops);

-- RAG recall: returns the top-N episodes by cosine similarity.  The signature
-- matches what Emma calls: match_episodes(query_embedding, match_count).
create or replace function public.match_episodes(
    query_embedding vector(384),
    match_count int
)
returns table (id text, content text, kind text, created_at timestamptz, similarity float)
language sql stable
as $$
    select e.id, e.content, e.kind, e.ts,
           1 - (e.embedding <=> query_embedding) as similarity
    from public.episodes e
    order by e.embedding <=> query_embedding
    limit match_count;
$$;

-- Note: RLS is left disabled so the service key (Emma's backend) can read and
-- write freely.  If you enable RLS, add a policy allowing the `anon` role
-- (or keep all access service-key-only).
