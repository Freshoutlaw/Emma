"""Web search — multi-backend (DuckDuckGo + Bing) + page text extraction.

Consults the network gate before any outbound request and routes through the
Guardian. No API keys required.

Backends are tried in order (DDG lite, DDG html, Bing) so search keeps
working when one engine rate-limits or serves an anti-bot page.

OPTIMIZATIONS:
- Connection pooling for HTTP clients
- Response caching for repeated searches
- Better timeout handling
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
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

    @staticmethod
    def _parse_results(html: str, n: int) -> list[dict]:
        """Parse DuckDuckGo results from either the lite or html front-end.

        lite: ``a.result-link`` anchors (+ ``td.result-snippet`` cells).
        html: ``div.result`` blocks (``a.result__a`` + ``a.result__snippet``).
        """
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict] = []
        for link in soup.select("a.result-link")[:n]:
            row = link.find_parent("tr")
            snippet_el = row.find("td", class_="result-snippet") if row else None
            results.append(
                {
                    "title": link.get_text(strip=True),
                    "url": link.get("href", ""),
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                }
            )
        if results:
            return results
        for result in soup.select("div.result")[:n]:
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
        return results

    @staticmethod
    def _parse_bing(html: str, n: int) -> list[dict]:
        """Parse Bing organic results (``li.b_algo``).

        Bing wraps organic links in ``/ck/a`` redirect URLs carrying the real
        address base64-encoded in the ``u=a1...`` query param — decode it so
        callers get the actual destination.
        """
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict] = []
        for li in soup.select("li.b_algo")[:n]:
            h2 = li.find("h2")
            anchor = h2.find("a", href=True) if h2 else None
            if not anchor:
                continue
            url = anchor.get("href", "")
            if "/ck/a" in url:
                match = re.search(r"u=a1([A-Za-z0-9_\-]+)", url)
                if match:
                    try:
                        padding = "=" * (-len(match.group(1)) % 4)
                        url = base64.urlsafe_b64decode(match.group(1) + padding).decode("utf-8", "replace")
                    except Exception:
                        pass
            snippet_el = li.find("p")
            results.append(
                {
                    "title": anchor.get_text(strip=True),
                    "url": url,
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                }
            )
        return results

    @staticmethod
    def _real_url(url: str) -> str:
        """Decode a DuckDuckGo ``uddg=`` redirect param into the real URL."""
        if "uddg=" in url:
            match = re.search(r"uddg=([^&]+)", url)
            if match:
                from urllib.parse import unquote

                return unquote(match.group(1))
        return url

    # ------------------------------------------------------------------ api
    async def search(self, query: str, n: int = 5) -> list[dict]:
        self._check_gate()
        self.guardian.guard("web_search", {"query": query})
        
        # Check cache first
        cache_key = self._cache_key(query, n)
        cache_entry = self._search_cache.get(cache_key)
        if cache_entry and (time.time() - cache_entry['timestamp']) < self._cache_ttl:
            return cache_entry['results']
        
        # Multi-backend: DDG serves two HTML front-ends (lite + html) and
        # intermittently answers with a 202 anti-bot page or a redirect; when
        # DDG blocks us, fall back to Bing.  Each backend gets one retry with a
        # short pause.  ``follow_redirects`` matters: both engines answer from
        # a redirect, and httpx does not follow by default (which used to leave
        # search with an empty body / 0 results).
        results: list[dict] = []
        client = self._get_client()
        backends = (
            (f"https://lite.duckduckgo.com/lite/?q={quote(query)}", self._parse_results),
            (f"https://html.duckduckgo.com/html/?q={quote(query)}", self._parse_results),
            (f"https://www.bing.com/search?q={quote(query)}&mkt=en-US", self._parse_bing),
        )
        for url, parser in backends:
            for attempt in range(2):
                try:
                    response = await client.get(url, follow_redirects=True)
                    response.raise_for_status()
                    results = parser(response.text, max(1, n))
                except Exception:
                    results = []
                if results:
                    break
                await asyncio.sleep(1.5)
            if results:
                break

        # Decode engine redirect params (uddg=) into real URLs where present.
        for result in results:
            result["url"] = self._real_url(result.get("url", ""))

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

    # ------------------------------------------------------- ollama registry
    async def search_ollama_registry(self, query: str, n: int = 10) -> list[dict]:
        """Search Ollama's public model registry (ollama.com) for a task.

        Returns ``[{"model": <tag family>, "description": <one-liner>}]`` so
        callers can assign a concrete model to a sub-agent (e.g. the user's
        "find the best free coding agent on ollama" flow).  Server-rendered,
        no API key; unlike the general search engines it is not anti-bot-gated.
        """
        self._check_gate()
        self.guardian.guard("web_search", {"query": query, "registry": "ollama"})
        client = self._get_client()
        url = f"https://ollama.com/search?q={quote(query)}&p=1"
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[dict] = []
        for link in soup.select("a[href^='/library/']"):
            model = link.get("href", "").replace("/library/", "")
            if not model or any(r["model"] == model for r in results):
                continue
            parent = link.find_parent("li") or link.find_parent("div")
            text = parent.get_text(" ", strip=True) if parent else ""
            description = text.replace(model, "", 1).strip()
            # cut the noisy pull/tag/updated tail after the first sentence
            for sep in (". ", " — ", " - "):
                if sep in description:
                    description = description.split(sep)[0] + "."
                    break
            results.append({"model": model, "description": description[:200]})
            if len(results) >= n:
                break
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
