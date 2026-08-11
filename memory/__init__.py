"""Emma memory — episodic store, embeddings, Supabase sync and RAG retrieval."""

from memory.embeddings import Embedder
from memory.episodic import EpisodicMemory
from memory.rag_pipeline import RAGPipeline
from memory.supabase_client import SupabaseClient, SupabaseError

__all__ = ["Embedder", "EpisodicMemory", "RAGPipeline", "SupabaseClient", "SupabaseError"]
