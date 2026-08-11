"""Hybrid LLM layer — local Ollama with cloud Groq fallback."""

from llm.router import LLMRouter, LLMUnavailable

__all__ = ["LLMRouter", "LLMUnavailable"]
