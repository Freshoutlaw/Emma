"""LLM cost pricing — per-model rates and cost computation.

Rates are USD per million tokens, taken from each provider's published pricing
(verified August 2026):

- Groq (groq.com/pricing): llama-3.3-70b-versatile $0.59/M input, $0.79/M
  output; llama-3.1-8b-instant $0.05/M input, $0.08/M output.
- Groq prompt caching (console.groq.com/docs/prompt-caching) is automatic and
  cached input tokens are billed at a **50% discount** — that discount is what
  the dashboard reports as cache savings.
- Ollama (local) is free.

Pricing changes over time. **This table is the thing to update** when a rate
changes; nothing else in the cost dashboard hard-codes a number.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

# model-prefix -> per-1M-token USD rates. `cache_read` is the discounted rate
# for cached input tokens (Groq: 50% of input); None = no known discount, so
# cached tokens are billed at the full input rate and savings report as 0.
MODEL_PRICING: dict[str, dict[str, Optional[float]]] = {
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79, "cache_read": 0.295},
    "llama-3.3-70b":           {"input": 0.59, "output": 0.79, "cache_read": 0.295},
    "llama-3.1-8b-instant":    {"input": 0.05, "output": 0.08, "cache_read": 0.025},
    "qwen3":                   {"input": 0.0,  "output": 0.0,  "cache_read": 0.0},   # Ollama local — free
    "nomic-embed-text":        {"input": 0.0,  "output": 0.0,  "cache_read": 0.0},   # Ollama local — free
}


def resolve_pricing(model: str) -> Optional[dict[str, Optional[float]]]:
    """Longest-prefix match of `model` against MODEL_PRICING.

    Model strings carry version suffixes (`llama-3.3-70b-versatile-fp8`,
    `qwen3:5.4b`) — match the longest known prefix so a dated/suffixed string
    still resolves. Unknown models return None (callers price them as $0 and
    warn); a row is still recorded so cost can be backfilled once a rate is
    added.
    """
    best: Optional[str] = None
    for key in MODEL_PRICING:
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    if best is None:
        return None
    return MODEL_PRICING[best]


def compute_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Cost in USD for one call.

    Input tokens that were served from cache are billed at the cache_read rate
    instead of the full input rate, so `input_tokens` may already include the
    cached portion (OpenAI/Groq usage shape). Unknown model → 0.0 + warning,
    never an exception.
    """
    rates = resolve_pricing(model)
    if rates is None:
        log.warning("cost: no pricing entry for model %r — recording at $0 (add it to cost/pricing.py)", model)
        return 0.0

    inp = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    cached = max(0, int(cache_read_tokens or 0))
    cw = max(0, int(cache_write_tokens or 0))

    input_rate = rates["input"] or 0.0
    output_rate = rates["output"] or 0.0
    cache_read_rate = rates["cache_read"]
    cache_write_rate = rates.get("cache_write")

    # cached tokens are a subset of input_tokens on OpenAI/Groq usage objects
    fresh = max(0, inp - cached)
    cost = fresh * input_rate + out * output_rate + cw * (cache_write_rate or input_rate)
    if cache_read_rate is not None:
        cost += cached * cache_read_rate
    else:
        cost += cached * input_rate  # no known discount — bill at full rate
    return cost / 1_000_000


def cache_savings(model: str, cache_read_tokens: int) -> float:
    """USD saved by prompt caching for this call.

    What the cached tokens would have cost at the full input rate minus what
    they cost at the discounted rate. Zero when no discount is known — never
    overstates savings.
    """
    rates = resolve_pricing(model)
    if rates is None:
        return 0.0
    cached = max(0, int(cache_read_tokens or 0))
    input_rate = rates["input"] or 0.0
    cache_read_rate = rates["cache_read"]
    if cache_read_rate is None:
        return 0.0
    return cached * (input_rate - cache_read_rate) / 1_000_000
