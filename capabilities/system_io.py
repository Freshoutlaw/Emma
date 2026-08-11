"""System I/O — file read/write and shell command execution.

All operations are gated through the Guardian:
- `read_file` / `list_dir`  → LOW (audited, never blocked)
- `file_write`              → MED, escalated to HIGH for sensitive paths
- `run_command`             → risk-assessed per command (read-only → LOW,
                              destructive patterns → HIGH, otherwise MED)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from security.guardian import Guardian

# Async file I/O when aiofiles is installed (it is in requirements.txt);
# otherwise fall back to synchronous I/O. `async_open` is the guard name
# referenced below — it must stay defined for both paths.
try:
    import aiofiles
    async_open = aiofiles.open
except ImportError:
    async_open = None


class CommandError(RuntimeError):
    def __init__(self, exit_code: int, stderr: str, command: str = "") -> None:
        super().__init__(f"command failed (exit {exit_code}): {stderr.strip()[:500]}")
        self.exit_code = exit_code
        self.stderr = stderr
        self.command = command


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    decision: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "decision": self.decision,
        }


class SystemIO:
    def __init__(self, guardian: Guardian, settings: Any = None) -> None:
        self.guardian = guardian
        self.settings = settings

    # ------------------------------------------------------------------ files
    async def read_file(self, path: str, max_bytes: int = 1_000_000) -> str:
        self.guardian.guard("read_file", {"path": path})
        target = Path(path).expanduser()
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_dir():
            raise IsADirectoryError(path)
        
        # Use async file I/O if available
        if async_open is not None:
            async with async_open(target, "rb") as fh:
                data = await fh.read(max_bytes + 1)
        else:
            with target.open("rb") as fh:
                data = fh.read(max_bytes + 1)
        
        if len(data) > max_bytes:
            data = data[:max_bytes]
        return data.decode("utf-8", errors="replace")

    async def write_file(self, path: str, content: str) -> dict:
        self.guardian.guard("file_write", {"path": path})
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        
        # Use async file I/O if available.  newline="" keeps writes
        # byte-exact (no CRLF translation on Windows) so that content
        # written here reads back identically via read_file().
        if async_open is not None:
            async with async_open(target, "w", encoding="utf-8", newline="") as fh:
                await fh.write(content)
        else:
            target.write_text(content, encoding="utf-8", newline="")
        
        return {"path": str(target), "bytes": len(content)}

    async def list_dir(self, path: str) -> list[dict]:
        self.guardian.guard("list_dir", {"path": path})
        target = Path(path).expanduser()
        if not target.exists():
            raise FileNotFoundError(path)
        if not target.is_dir():
            raise NotADirectoryError(path)
        entries = []
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                stat = entry.stat()
                entries.append(
                    {
                        "name": entry.name,
                        "type": "dir" if entry.is_dir() else "file",
                        "size": stat.st_size if entry.is_file() else None,
                        "mtime": stat.st_mtime,
                    }
                )
            except OSError:
                continue
        return entries

    async def file_exists(self, path: str) -> bool:
        return Path(path).expanduser().exists()

    # ------------------------------------------------------------------ shell
    async def run_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: int = 120,
    ) -> CommandResult:
        decision = self.guardian.guard("run_command", {"command": command, "cwd": cwd})
        started = time.perf_counter()
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            exit_code = proc.returncode if proc.returncode is not None else 0
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise CommandError(-1, "command timed out", command) from None
        duration_ms = int((time.perf_counter() - started) * 1000)
        return CommandResult(
            exit_code=exit_code,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=duration_ms,
            decision=decision.to_dict(),
        )
