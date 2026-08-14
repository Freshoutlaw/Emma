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

        def _grab():
            # PyAutoGUI is the primary grabber; fall back to Pillow's
            # ImageGrab (works on Windows without pyautogui) so screen
            # capture still works on machines without the optional dep.
            try:
                import pyautogui
            except ImportError:
                pyautogui = None
            if pyautogui is not None:
                return pyautogui.screenshot()
            try:
                from PIL import ImageGrab
            except ImportError as exc:
                raise RuntimeError(
                    "desktop screenshots need pyautogui or Pillow — "
                    "run `pip install 'emma-ai[desktop]'` or `pip install pillow`"
                ) from exc
            return ImageGrab.grab()

        def _shot() -> bytes:
            image = _grab()
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

    async def open_app(self, app: str) -> dict:
        """Open an application by name (Windows/macOS/Linux)."""
        self.guardian.guard("desktop_control", {"action": "open_app", "app": app})
        system = platform.system().lower()
        
        if system == "windows":
            cmd = ["powershell", "-Command", f"Start-Process '{app}'"]
        elif system == "darwin":
            cmd = ["open", "-a", app]
        elif system == "linux":
            cmd = ["xdg-open", app]
        else:
            return {"success": False, "reason": f"unsupported platform: {system}"}
        
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await proc.wait()
        return {"success": proc.returncode == 0, "platform": system}

    async def close_app(self, app: str) -> dict:
        """Close an application by name (Windows/macOS/Linux)."""
        self.guardian.guard("desktop_control", {"action": "close_app", "app": app})
        system = platform.system().lower()
        
        if system == "windows":
            cmd = ["powershell", "-Command", f"Get-Process '{app}' | Stop-Process -Force"]
        elif system == "darwin":
            cmd = ["pkill", "-x", app]
        elif system == "linux":
            cmd = ["pkill", "-x", app]
        else:
            return {"success": False, "reason": f"unsupported platform: {system}"}
        
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await proc.wait()
        return {"success": proc.returncode == 0, "platform": system}
