"""Memory agent — stores and recalls Emma's episodic memories.

Natural-language entry points:
- "remember that <X>" / "store <X>"  → persist an episode
- "recall <query>" / "what do you remember about <X>" → retrieve top matches
- "show memory" / "recent memories"  → last N episodes (and shows the panel)
- anything else                      → stored as a user episode

Recalls and recent-memory requests push the memory panel to the HUD, because
that is exactly when the operator wants to see it.
"""

from __future__ import annotations

from agents.base import AgentResult, BaseAgent


class MemoryAgent(BaseAgent):
    name = "memory"
    description = "Stores and recalls episodic memories."

    # ---------------------------------------------------------------- api
    async def store(self, content: str, kind: str = "episode", payload: dict | None = None) -> AgentResult:
        episode_id = await self.pipeline.episodic.remember(content, kind=kind, payload=payload)
        self._audit("memory.stored", action="store", detail={"id": episode_id, "kind": kind})
        return AgentResult(
            ok=True,
            output=f"Stored {kind} memory #{episode_id}.",
            intent="memory",
            memory_ids=[episode_id],
        )

    async def recall(self, query: str, k: int = 5) -> AgentResult:
        items = await self.pipeline.episodic.recall(query, k=k)
        self.pipeline.display.set("memory", reason="operator request")
        if not items:
            return AgentResult(ok=True, output="No matching memories found.", intent="memory")
        lines = [
            f"[{i}] (score {item.get('score', 0):.3f}) {item.get('ts', '')} — {item.get('content', '')}"
            for i, item in enumerate(items, start=1)
        ]
        return AgentResult(ok=True, output="Memories:\n" + "\n".join(lines), intent="memory")

    async def recent(self, limit: int = 20) -> AgentResult:
        items = self.pipeline.episodic.recent(limit=limit)
        self.pipeline.display.set("memory", reason="operator request")
        if not items:
            return AgentResult(ok=True, output="No memories stored yet.", intent="memory")
        lines = [f"[{i}] {item.get('ts', '')} ({item.get('kind', 'episode')}) — {item.get('content', '')}" for i, item in enumerate(items, start=1)]
        return AgentResult(ok=True, output="Recent memories:\n" + "\n".join(lines), intent="memory")

    # ---------------------------------------------------------------- run
    async def run(self, request: str) -> AgentResult:
        low = request.strip().lower()
        if low.startswith("recall"):
            query = request.split(" ", 1)[1].strip() if " " in request else request
            return await self.recall(query)
        if low.startswith("remember") or low.startswith("store"):
            content = request.split(" ", 1)[1].strip() if " " in request else request
            return await self.store(content)
        if "recent" in low or ("show" in low and "memory" in low):
            return await self.recent()
        # Default: store what the user said as a user episode.
        return await self.store(request, kind="user")
