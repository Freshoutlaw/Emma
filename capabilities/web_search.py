"""Web search — DuckDuckGo HTML search + page text extraction.

Consults the network gate before any outbound request and routes through the
Guardian. No API keys required.

OPTIMIZATIONS:
- Connection pooling for HTTP clients
- Response caching for repeated searches
- Better timeout handling
"""

from __future__ import annotations

import hashlib
import time
from typing import Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from flags.network_gate import NetworkGate
from security.guardian import Guardian


class NetworkBlocked(RuntimeError):
    pass


class WebSearch:
    def __init__(self, guardian: Guardian, gate: Optional[NetworkGate] = None, timeout: float = 10.0) -> None:
        self.guardian = guardian
        self.gate = gate
        self.timeout = timeout
        # Use connection pooling for better performance
        self._client: Optional[httpx.AsyncClient] = None
        # Simple cache for search results
        self._search_cache = {}
        self._cache_ttl = 300  # 5 minutes
        
    def _get_client(self) -> httpx.AsyncClient:
        """Get or create a pooled HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_keepalive_connections=3, max_connections=6)
            )
        return self._client
    
    def _cache_key(self, query: str, n: int) -> str:
        """Generate cache key for search."""
        return f"{query}:{n}:{hashlib.md5(query.encode()).hexdigest()}"
    
    async def close(self) -> None:
        """Clean up HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }

    # ------------------------------------------------------------------ gate
    def _check_gate(self) -> None:
        if self.gate is not None and not self.gate.is_open:
            raise NetworkBlocked("network egress is closed by the network gate")

    # ------------------------------------------------------------------ api
    async def search(self, query: str, n: int = 5) -> list[dict]:
        self._check_gate()
        self.guardian.guard("web_search", {"query": query})
        
        # Check cache first
        cache_key = self._cache_key(query, n)
        cache_entry = self._search_cache.get(cache_key)
        if cache_entry and (time.time() - cache_entry['timestamp']) < self._cache_ttl:
            return cache_entry['results']
        
        url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
        client = self._get_client()
        response = await client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for result in soup.select("div.result")[: max(1, n)]:
            anchor = result.select_one("a.result__a")
            if not anchor:
                continue
            snippet_el = result.select_one("a.result__snippet")
            results.append(
                {
                    "title": anchor.get_text(strip=True),
                    "url": anchor.get("href", ""),
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                }
            )
        
        # Cache the results
        self._search_cache[cache_key] = {
            'timestamp': time.time(),
            'results': results
        }
        
        # Clean old cache entries
        if len(self._search_cache) > 100:
            oldest_keys = sorted(self._search_cache.keys(), 
                                key=lambda k: self._search_cache[k]['timestamp'])[:20]
            for key in oldest_keys:
                del self._search_cache[key]
        
        return results

    async def fetch_page_text(self, url: str, max_chars: int = 8000) -> str:
        self._check_gate()
        self.guardian.guard("web_search", {"url": url})
        client = self._get_client()
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ", strip=True).split())
        return text[:max_chars]
