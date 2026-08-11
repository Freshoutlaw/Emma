"""Desktop control — notifications, mouse/keyboard automation, screenshots.

Mouse/keyboard automation uses PyAutoGUI (optional); notifications use native
platform tools so they work without extra dependencies. Everything is gated
through the Guardian.
"""

from __future__ import annotations

import asyncio
import platform
import subprocess
from pathlib import Path
from typing import Optional

from security.guardian import Guardian


class DesktopControl:
    def __init__(self, guardian: Guardian) -> None:
        self.guardian = guardian

    # ------------------------------------------------------------------ setup
    def available(self) -> bool:
        try:
            import pyautogui  # noqa: F401

            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------ api
    async def notify(self, title: str, message: str) -> dict:
        self.guardian.guard("desktop_control", {"action": "notify", "title": title})
        system = platform.system().lower()
        if system == "darwin":
            script = f'display notification "{message}" with title "{title}"'
            cmd = ["osascript", "-e", script]
        elif system == "linux":
            cmd = ["notify-send", title, message]
        elif system == "windows":
            cmd = [
                "powershell",
                "-Command",
                f'New-BurntToastNotification -Text "{title}", "{message}"',
            ]
        else:
            return {"sent": False, "reason": f"unsupported platform: {system}"}
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await proc.wait()
        return {"sent": proc.returncode == 0, "platform": system}

    async def screenshot(self, path: Optional[str] = None) -> bytes:
        self.guardian.guard("desktop_control", {"action": "screenshot"})
        if not self.available():
            raise RuntimeError("pyautogui is not installed — run `pip install 'emma-ai[desktop]'`")
        import pyautogui

        def _shot() -> bytes:
            image = pyautogui.screenshot()
            if path:
                target = Path(path).expanduser()
                target.parent.mkdir(parents=True, exist_ok=True)
                image.save(target)
            import io

            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()

        return await asyncio.to_thread(_shot)

    async def type_text(self, text: str, interval: float = 0.02) -> None:
        self.guardian.guard("desktop_control", {"action": "type_text"})
        if not self.available():
            raise RuntimeError("pyautogui is not installed — run `pip install 'emma-ai[desktop]'`")
        import pyautogui

        await asyncio.to_thread(pyautogui.typewrite, text, interval)

    async def click(self, x: int, y: int, button: str = "left") -> None:
        self.guardian.guard("desktop_control", {"action": "click", "x": x, "y": y})
        if not self.available():
            raise RuntimeError("pyautogui is not installed — run `pip install 'emma-ai[desktop]'`")
        import pyautogui

        await asyncio.to_thread(pyautogui.click, x, y, button=button)

    async def move_mouse(self, x: int, y: int) -> None:
        self.guardian.guard("desktop_control", {"action": "move_mouse", "x": x, "y": y})
        if not self.available():
            raise RuntimeError("pyautogui is not installed — run `pip install 'emma-ai[desktop]'`")
        import pyautogui

        await asyncio.to_thread(pyautogui.moveTo, x, y)
