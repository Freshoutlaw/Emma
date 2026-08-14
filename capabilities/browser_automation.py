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
        self._browser = await self._launch()
        self._page = await self._browser.new_page()

    async def _launch(self):
        """Launch a headless Chromium, falling back to the system Edge/Chrome.

        Playwright's bundled chromium needs `playwright install chromium`
        (~150MB download).  On machines where that hasn't been run (e.g. a
        Windows box with Edge preinstalled), use the system browser instead
        so screenshot/automation tools work out of the box.
        """
        import os

        candidates: list[dict] = [{}]  # bundled chromium (dev machines)
        candidates.append({"channel": "msedge"})  # installed Edge
        for path in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ):
            if os.path.exists(path):
                candidates.append({"executable_path": path})
                break
        last_error: Optional[Exception] = None
        for kwargs in candidates:
            try:
                return await self._playwright.chromium.launch(headless=True, **kwargs)
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(
            "no chromium available — run `playwright install chromium` "
            "or install Microsoft Edge / Google Chrome"
        )

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

    async def scroll(self, pixels: int = 500) -> dict:
        """Scroll the page by given pixels (positive = down, negative = up)."""
        self.guardian.guard("browser_automation", {"action": "scroll", "pixels": pixels})
        self._check()
        await self._ensure()
        await self._page.evaluate(f"window.scrollBy(0, {pixels})")
        return {"scrolled": pixels}

    async def go_back(self) -> dict:
        """Navigate back in browser history."""
        self.guardian.guard("browser_automation", {"action": "go_back"})
        self._check()
        await self._ensure()
        await self._page.go_back()
        return {"url": self._page.url, "title": await self._page.title()}

    async def go_forward(self) -> dict:
        """Navigate forward in browser history."""
        self.guardian.guard("browser_automation", {"action": "go_forward"})
        self._check()
        await self._ensure()
        await self._page.go_forward()
        return {"url": self._page.url, "title": await self._page.title()}

    async def hover(self, selector: str) -> dict:
        """Hover over an element."""
        self.guardian.guard("browser_automation", {"action": "hover", "selector": selector})
        self._check()
        await self._ensure()
        await self._page.hover(selector, timeout=10000)
        return {"hovered": selector}

    async def get_text(self, selector: str) -> str:
        """Get text content of a specific element."""
        self.guardian.guard("browser_automation", {"action": "get_text", "selector": selector})
        self._check()
        await self._ensure()
        return await self._page.inner_text(selector)

    async def get_attribute(self, selector: str, attribute: str) -> str:
        """Get attribute value of an element."""
        self.guardian.guard("browser_automation", {"action": "get_attribute", "selector": selector, "attribute": attribute})
        self._check()
        await self._ensure()
        return await self._page.get_attribute(selector, attribute) or ""

    async def press_key(self, key: str) -> dict:
        """Press a keyboard key (e.g., 'Enter', 'Escape', 'ArrowDown')."""
        self.guardian.guard("browser_automation", {"action": "press_key", "key": key})
        self._check()
        await self._ensure()
        await self._page.keyboard.press(key)
        return {"pressed": key}

    async def select_option(self, selector: str, value: str) -> dict:
        """Select an option from a dropdown."""
        self.guardian.guard("browser_automation", {"action": "select_option", "selector": selector, "value": value})
        self._check()
        await self._ensure()
        await self._page.select_option(selector, value)
        return {"selected": value}
