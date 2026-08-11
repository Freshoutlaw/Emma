"""Orchestration Tier 1 — routing policy.

Sits above the intent classifier and decides *how* to route:

1. **Priority ordering** — when an ambiguous request could match multiple
   intents, pick the one that matches the strongest signal (keyword > LLM).
2. **Decomposition** — when a request naturally contains multiple steps
   (e.g. "remember X and also list the files in /tmp"), split into
   independent sub-requests that can be dispatched separately.
3. **Clarify-on-ambiguous** — when the classifier is below confidence
   threshold AND keyword matching is a fallback, return a clarification
   prompt instead of guessing.

This module is stateless — all decisions are pure functions of the
classification result and the message text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Intent priority — higher number wins on tie. When two intents match
# equally well (both keyword-based), the higher-priority one wins.
INTENT_PRIORITY: dict[str, int] = {
    "security": 100,      # safety-critical — always wins
    "kill_switch": 100,
    "self_improve": 80,   # code modification — high priority
    "control": 70,        # explicit action — usually intentional
    "map": 60,            # location/weather — usually intentional
    "supabase_query": 60, # database query — usually intentional
    "design": 50,         # design task — usually intentional
    "memory": 40,         # memory ops — often mixed with other intents
    "reasoning": 20,      # fallback — lowest priority
    "chat": 10,           # pure conversation — lowest
}

# Confidence threshold for LLM classifications. Below this, we check
# whether a keyword match exists and prefer it over the LLM guess.
LOW_CONFIDENCE_THRESHOLD = 0.5

# Decomposition separators — these indicate the message likely contains
# multiple independent requests.
DECOMPOSITION_SEPARATORS = (
    r"\band also\b",
    r"\band\b.*\b(also|then|after that|next)\b",
    r"\bplus\b",
    r"\badditionally\b",
    r"\bwhile you'?re at it\b",
    r"[;,]\s*(?:also|then|and|plus)\b",
)


@dataclass
class RoutingDecision:
    """A routing decision from the policy layer."""

    # The intent(s) to dispatch to, in priority order.
    intents: list[str]
    # Original or decomposed messages, one per intent.
    messages: list[str]
    # Whether this request was decomposed.
    decomposed: bool = False
    # If clarification is needed, this prompt replaces dispatch.
    clarify: Optional[str] = None
    # Routing metadata for audit.
    reason: str = "direct"


def detect_decomposition(message: str) -> list[str]:
    """Split a multi-part request into independent sub-requests.

    Returns a single-item list if no decomposition is detected.
    """
    # Try each separator pattern.
    for pattern in DECOMPOSITION_SEPARATORS:
        parts = re.split(pattern, message, flags=re.IGNORECASE)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) > 1:
            return parts
    return [message]


def priority_tiebreak(intents: list[str]) -> str:
    """Pick the highest-priority intent from a list of tied intents."""
    return max(intents, key=lambda i: INTENT_PRIORITY.get(i, 0))


def maybe_clarify(
    message: str,
    keyword_intent: str,
    llm_intent: Optional[str],
    llm_confidence: Optional[float],
) -> Optional[str]:
    """Return a clarification prompt if the classification is ambiguous.

    Returns None if routing is clear enough to proceed.
    """
    # If keyword matched decisively, no clarification needed.
    if keyword_intent != "reasoning":
        return None

    # If LLM was used and confidence is high, trust it.
    if llm_intent and llm_confidence is not None and llm_confidence >= LOW_CONFIDENCE_THRESHOLD:
        return None

    # If the LLM returned an intent but keyword says reasoning, and the
    # message is short (likely ambiguous), ask for clarification.
    low = message.lower().strip()
    if llm_intent and llm_intent != "reasoning" and len(low.split()) <= 5:
        return (
            f"I think you might want to {llm_intent}, but I'm not sure. "
            f"Could you clarify? For example: "
            f"\"{llm_intent}: {low}\" or describe what you need."
        )

    return None


def apply_policy(
    message: str,
    keyword: str,
    llm_result: Optional[dict] = None,
) -> RoutingDecision:
    """Apply routing policy to a classified request.

    Args:
        message: The original user message.
        keyword: The keyword-matched intent.
        llm_result: The LLM classification result, if available.

    Returns:
        A RoutingDecision with the final intent(s) and message(s).
    """
    llm_intent = (llm_result or {}).get("intent")
    llm_confidence = (llm_result or {}).get("confidence")

    # Step 1: Check for decomposition.
    parts = detect_decomposition(message)
    decomposed = len(parts) > 1

    if decomposed:
        # Classify each part independently. For now, re-run keyword
        # matching on each sub-message. The caller can optionally
        # run the LLM on each part too.
        intents = []
        messages = []
        for part in parts:
            part_keyword = _quick_keyword(part)
            intents.append(part_keyword)
            messages.append(part)
        return RoutingDecision(
            intents=intents,
            messages=messages,
            decomposed=True,
            reason="decomposed",
        )

    # Step 2: Tiebreak if both keyword and LLM agree on different intents.
    if llm_intent and llm_intent != keyword and keyword != "reasoning":
        # Keyword matched something specific; trust keyword (it's faster
        # and more reliable for clear signals).
        return RoutingDecision(
            intents=[keyword],
            messages=[message],
            reason="keyword_priority",
        )

    if llm_intent and llm_intent != "reasoning" and keyword == "reasoning":
        # Only LLM found a match; trust it if confidence is decent.
        conf = llm_confidence or 0.0
        if conf >= LOW_CONFIDENCE_THRESHOLD:
            return RoutingDecision(
                intents=[llm_intent],
                messages=[message],
                reason="llm_resolved",
            )

    # Step 3: Clarify-on-ambiguous.
    clarify = maybe_clarify(message, keyword, llm_intent, llm_confidence)
    if clarify:
        return RoutingDecision(
            intents=["clarify"],
            messages=[message],
            clarify=clarify,
            reason="ambiguous",
        )

    # Step 4: Default — use keyword, fall back to reasoning.
    final_intent = keyword if keyword != "reasoning" else (llm_intent or "reasoning")
    return RoutingDecision(
        intents=[final_intent],
        messages=[message],
        reason="default",
    )


def _quick_keyword(message: str) -> str:
    """Fast keyword-only intent classification (no LLM).

    Extracted from router.keyword_intent for use in decomposition.
    """
    low = message.lower()
    # Panel shortcuts.
    panel = re.match(
        r"^(?:show|open|display|hide|close)[ \t]+(?:the[ \t]+)?"
        r"(memory|status|guardian|security|map|panels?)(?=[ \t]|$)",
        low,
    )
    if panel:
        if panel.group(1) == "map":
            return "map"
        return "security" if panel.group(1) in ("status", "guardian", "security", "panel", "panels") else "memory"

    if any(w in low for w in ("remember", "recall", "memory", "what did i")):
        return "memory"
    _map_words = ("map", "maps", "where is", "where's", "weather in", "flight", "flights")
    if any(w in low for w in _map_words):
        return "map"
    if any(w in low for w in ("kill switch", "security", "guardian", "consent", "network gate")):
        return "security"
    if any(w in low for w in ("query", "supabase", "database", "table", "sql")):
        return "supabase_query"
    if any(w in low for w in ("design", "mockup", "scaffold", "tokens", "catalog")):
        return "design"
    if (
        any(w in low for w in ("improve yourself", "self-improve", "review your code"))
        or low.startswith(("apply ", "verify ", "inspect "))
        or "apply patch" in low
    ):
        return "self_improve"
    if any(w in low for w in ("run ", "execute", "shell", "terminal", "file", "files", "docker", "git ", "mqtt", "browser", "search the web")):
        return "control"
    return "reasoning"
