"""Docker operations — containers, images, logs, compose.

Thin wrapper over `SystemIO.run_command`; every invocation passes through the
Guardian. Destructive docker commands (`rm`, `rmi`, `prune`) are additionally
escalated to HIGH by the guardian's pattern matcher.
"""

from __future__ import annotations

import shlex
from typing import Optional

from capabilities.system_io import CommandError, SystemIO


class DockerManager:
    def __init__(self, io: SystemIO) -> None:
        self.io = io

    async def _docker(self, args: list[str], cwd: Optional[str] = None, timeout: int = 300) -> str:
        self.io.guardian.guard("docker", {"args": args})
        command = "docker " + " ".join(shlex.quote(a) for a in args)
        result = await self.io.run_command(command, cwd=cwd, timeout=timeout)
        if result.exit_code != 0:
            raise CommandError(result.exit_code, result.stderr, command)
        return result.stdout

    # ------------------------------------------------------------------ read
    async def ps(self) -> str:
        return await self._docker(["ps", "-a"])

    async def images(self) -> str:
        return await self._docker(["images"])

    async def logs(self, container: str, tail: int = 200) -> str:
        return await self._docker(["logs", "--tail", str(tail), container])

    # ------------------------------------------------------------------ compose
    async def compose_up(self, directory: str, detached: bool = True) -> str:
        args = ["compose", "up", "-d"] if detached else ["compose", "up"]
        return await self._docker(args, cwd=directory, timeout=600)

    async def compose_down(self, directory: str) -> str:
        return await self._docker(["compose", "down"], cwd=directory, timeout=300)

    async def compose_ps(self, directory: str) -> str:
        return await self._docker(["compose", "ps"], cwd=directory)

    # ------------------------------------------------------------------ run
    async def run(self, image: str, *args: str, detach: bool = True) -> str:
        cmd = ["run", "-d" if detach else "--rm", image, *args]
        return await self._docker(cmd, timeout=600)
