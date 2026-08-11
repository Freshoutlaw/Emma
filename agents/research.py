"""Agent Factory — Tier 1 Research Agent.

A specialist agent that performs web research: searches for information,
reads relevant pages, and synthesizes findings into a structured report.

This is the first "factory-produced" agent — it demonstrates the pattern
that the Agent Factory will use to generate new agents on demand.

Tool allowlist: web_search, fetch_page, read_file, list_dir (read-only
tools for research — no writes, no execution).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agents.base import AgentResult, BaseAgent

if TYPE_CHECKING:
    from agents.router import Pipeline


class ResearchAgent(BaseAgent):
    name = "research"
    description = "Performs web research: searches, reads pages, and synthesizes findings."

    # Least-privilege: read-only tools for research.
    tool_allowlist = frozenset({
        "web_search",
        "fetch_page",
        "read_file",
        "list_dir",
    })

    # Maximum number of search results to process per query.
    MAX_SEARCH_RESULTS = 5
    # Maximum characters to extract from each page.
    MAX_PAGE_CHARS = 8000
    # Maximum number of pages to read per research task.
    MAX_PAGES = 3

    def __init__(self, pipeline: "Pipeline") -> None:
        super().__init__(pipeline)

    async def run(self, request: str) -> AgentResult:
        """Execute a research task.

        The request can be:
        - A natural language question: "What are the best practices for X?"
        - A search-focused request: "search for Y"
        - A synthesis request: "summarize what you find about Z"
        """
        self._audit("research.started", detail={"request": request[:200]})

        try:
            # Step 1: Search the web.
            search_results = await self._search(request)

            if not search_results:
                return AgentResult(
                    ok=True,
                    output=f"No search results found for: {request}",
                    intent="research",
                )

            # Step 2: Read the most relevant pages.
            pages = await self._read_pages(search_results)

            # Step 3: Synthesize findings.
            synthesis = self._synthesize(request, search_results, pages)

            self._audit("research.completed", detail={
                "searches": len(search_results),
                "pages_read": len(pages),
                "output_length": len(synthesis),
            })

            return AgentResult(
                ok=True,
                output=synthesis,
                intent="research",
                actions=[
                    {"tool": "web_search", "args": {"query": request}},
                    *[{"tool": "fetch_page", "args": {"url": p["url"]}} for p in pages],
                ],
            )

        except Exception as exc:
            self._audit("research.failed", detail={"error": str(exc)})
            return AgentResult(
                ok=False,
                output=f"Research failed: {exc}",
                intent="research",
                error=str(exc),
            )

    async def _search(self, query: str) -> list[dict]:
        """Search the web for the query."""
        try:
            output = await self.pipeline.control.execute(
                "web_search",
                actor="research",
                query=query,
                n=self.MAX_SEARCH_RESULTS,
            )
            # Parse the search results (web_search returns JSON string).
            if isinstance(output, str):
                results = json.loads(output)
            else:
                results = output
            if isinstance(results, list):
                return results[:self.MAX_SEARCH_RESULTS]
            return []
        except Exception as exc:
            self._audit("research.search_failed", detail={"error": str(exc)})
            return []

    async def _read_pages(self, search_results: list[dict]) -> list[dict]:
        """Read the most relevant pages from search results."""
        pages = []
        urls_seen = set()

        for result in search_results[:self.MAX_PAGES]:
            url = result.get("url") or result.get("link") or ""
            if not url or url in urls_seen:
                continue
            urls_seen.add(url)

            try:
                output = await self.pipeline.control.execute(
                    "fetch_page",
                    actor="research",
                    url=url,
                )
                # Truncate to max chars.
                if isinstance(output, str) and len(output) > self.MAX_PAGE_CHARS:
                    output = output[:self.MAX_PAGE_CHARS] + "\n[truncated]"

                pages.append({
                    "url": url,
                    "title": result.get("title", ""),
                    "snippet": result.get("snippet", result.get("content", "")),
                    "content": output if isinstance(output, str) else str(output),
                })
            except Exception as exc:
                # Skip pages that fail to load — not fatal.
                self._audit("research.page_failed", detail={"url": url, "error": str(exc)})

        return pages

    def _synthesize(
        self, query: str, search_results: list[dict], pages: list[dict]
    ) -> str:
        """Synthesize search results and page content into a report."""
        lines = [f"## Research: {query}\n"]

        # Summary from search results.
        if search_results:
            lines.append("### Search Results\n")
            for i, r in enumerate(search_results, 1):
                title = r.get("title", "Untitled")
                url = r.get("url") or r.get("link", "")
                snippet = r.get("snippet") or r.get("content", "")
                lines.append(f"{i}. **{title}**")
                if url:
                    lines.append(f"   {url}")
                if snippet:
                    lines.append(f"   {snippet[:200]}")
                lines.append("")

        # Detailed findings from pages.
        if pages:
            lines.append("### Detailed Findings\n")
            for page in pages:
                title = page.get("title") or page["url"]
                lines.append(f"**{title}**")
                lines.append(f"Source: {page['url']}\n")
                # Take the first ~500 chars of content as the key finding.
                content = page.get("content", "")
                if content:
                    # Find the first meaningful paragraph.
                    paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 50]
                    if paragraphs:
                        lines.append(paragraphs[0][:500])
                    else:
                        lines.append(content[:500])
                lines.append("")

        # Footer with metadata.
        lines.append("---")
        lines.append(f"*Research completed: {len(search_results)} results, "
                     f"{len(pages)} pages read*")

        return "\n".join(lines)
