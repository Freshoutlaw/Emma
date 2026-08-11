"""Git operations — status, diff, log, commit, push.

Thin wrapper over `SystemIO.run_command` so every git invocation passes
through the Guardian (push escalates to HIGH via the destructive pattern
and/or the explicit `git_push` gate).
"""

from __future__ import annotations

import shlex
from typing import Optional

from capabilities.system_io import CommandError, SystemIO


class GitManager:
    def __init__(self, io: SystemIO) -> None:
        self.io = io

    def _cmd(self, cwd: Optional[str], *args: str) -> str:
        parts = ["git"]
        if cwd:
            parts += ["-C", cwd]
        parts += list(args)
        return " ".join(shlex.quote(p) for p in parts)

    async def _run(self, cwd: Optional[str], *args: str, timeout: int = 60) -> str:
        result = await self.io.run_command(self._cmd(cwd, *args), cwd=cwd, timeout=timeout)
        if result.exit_code != 0:
            raise CommandError(result.exit_code, result.stderr, self._cmd(cwd, *args))
        return result.stdout

    # ------------------------------------------------------------------ read
    async def status(self, cwd: Optional[str] = None) -> str:
        return await self._run(cwd, "status")

    async def diff(self, cwd: Optional[str] = None) -> str:
        return await self._run(cwd, "diff")

    async def log(self, n: int = 10, cwd: Optional[str] = None) -> str:
        return await self._run(cwd, "log", f"-{n}", "--oneline")

    async def branch(self, cwd: Optional[str] = None) -> str:
        return await self._run(cwd, "branch", "-a")

    # ------------------------------------------------------------------ write
    async def commit(self, message: str, paths: Optional[list[str]] = None, cwd: Optional[str] = None) -> str:
        self.io.guardian.guard("git_commit", {"message": message, "cwd": cwd, "paths": paths})
        if paths:
            add = self._cmd(cwd, "add", *paths)
        else:
            add = self._cmd(cwd, "add", "-A")
        add_result = await self.io.run_command(add, cwd=cwd, timeout=60)
        if add_result.exit_code != 0:
            raise CommandError(add_result.exit_code, add_result.stderr, add)
        return await self._run(cwd, "commit", "-m", message)

    async def push(self, remote: str = "origin", branch: Optional[str] = None, cwd: Optional[str] = None) -> str:
        self.io.guardian.guard("git_push", {"remote": remote, "branch": branch, "cwd": cwd})
        args = ["push", remote]
        if branch:
            args.append(branch)
        return await self._run(cwd, *args, timeout=120)
