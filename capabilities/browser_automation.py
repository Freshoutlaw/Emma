"""Browser automation — Playwright-driven headless Chromium.

Optional dependency (`pip install 'emma-ai[browser]'` or `playwright`); all
methods degrade with a clear error when Playwright is absent. Every action is
gated through the Guardian and the network gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from flags.network_gate import NetworkGate
from security.guardian import Guardian
from capabilities.web_search import NetworkBlocked


class BrowserAutomation:
    def __init__(self, guardian: Guardian, gate: Optional[NetworkGate] = None) -> None:
        self.guardian = guardian
        self.gate = gate
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    # ------------------------------------------------------------------ setup
    def available(self) -> bool:
        try:
            import playwright  # noqa: F401

            return True
        except ImportError:
            return False

    def _check(self) -> None:
        if not self.available():
            raise RuntimeError("playwright is not installed — run `pip install playwright && playwright install chromium`")
        if self.gate is not None and not self.gate.is_open:
            raise NetworkBlocked("network egress is closed by the network gate")

    async def _ensure(self) -> None:
        if self._page is not None:
            return
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()

    # ------------------------------------------------------------------ api
    async def open(self, url: str) -> dict:
        self.guardian.guard("browser_automation", {"url": url})
        self._check()
        await self._ensure()
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return {"title": await self._page.title(), "url": self._page.url}

    async def extract_text(self) -> str:
        self.guardian.guard("browser_automation", {"action": "extract_text"})
        self._check()
        await self._ensure()
        return await self._page.inner_text("body")

    async def screenshot(self, path: Optional[str] = None) -> bytes:
        self.guardian.guard("browser_automation", {"action": "screenshot"})
        self._check()
        await self._ensure()
        data = await self._page.screenshot()
        if path:
            target = Path(path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return data

    async def click(self, selector: str) -> None:
        self.guardian.guard("browser_automation", {"action": "click", "selector": selector})
        self._check()
        await self._ensure()
        await self._page.click(selector, timeout=10000)

    async def fill(self, selector: str, value: str) -> None:
        self.guardian.guard("browser_automation", {"action": "fill", "selector": selector})
        self._check()
        await self._ensure()
        await self._page.fill(selector, value)

    async def close(self) -> None:
        for handle in (self._browser, self._playwright):
            try:
                if handle is not None:
                    await handle.close()
            except Exception:
                pass
        self._browser = None
        self._page = None
        self._playwright = None
