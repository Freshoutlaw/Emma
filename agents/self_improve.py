"""Self-improvement agent — Emma reading, reviewing and modifying her own code.

Modifications are restricted to the project tree and gated behind the
`self_modify` consent rule (HIGH severity by default). Before any write the
file's current content is snapshotted — via a git commit when the project is
a git repo, otherwise a `.bak` copy under `data/backups/` — and the change
is audit-logged.

Every applied patch runs a **verification loop**: the changed file is
syntax-checked (`py_compile`) and, when pytest is available, the test suite
is executed. If any check fails, the snapshot is restored (`git checkout` in
repo mode, the `.bak` copy otherwise) and the rollback is audit-logged — a
broken Emma never survives an edit.  Successful repo-mode patches are
committed so the working tree stays clean and every self-modify is a
reviewable commit.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from agents.base import AgentResult, BaseAgent

SOURCE_DIRS = ("backend", "agents", "capabilities", "security", "llm", "memory", "flags")

REVIEW_SYSTEM_PROMPT = (
    "You are Emma reviewing your own source code to become better.\n"
    "You HAVE self-modification capabilities. You can read your own source code, review it, "
    "suggest improvements, and apply patches to make yourself better.\n"
    "Read the files provided and produce a concrete list of improvement ideas.\n"
    "For each idea include: the file, what to change, and why. Be specific and practical.\n"
    "Do not claim bugs exist without evidence in the code shown.\n"
    "Never deny your self-modification capabilities - they are a core feature of your architecture."
)

# Exit code pytest uses when zero tests were collected — not a failure.
PYTEST_NO_TESTS = 5


class SelfImproveAgent(BaseAgent):
    name = "self_improve"
    description = "Reads and modifies Emma's own code to improve over time."

    # ---------------------------------------------------------------- helpers
    def _resolve(self, path: str) -> Path:
        root = self.pipeline.settings.project_root.resolve()
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if str(resolved) != str(root) and not str(resolved).startswith(str(root) + str(Path("/"))):
            raise ValueError(f"self-modification is restricted to the project tree ({root})")
        return resolved

    def _pick_files(self, limit: int = 8) -> list[Path]:
        root = self.pipeline.settings.project_root
        files: list[Path] = []
        for directory in SOURCE_DIRS:
            base = root / directory
            if not base.exists():
                continue
            for path in sorted(base.rglob("*.py")):
                if "__pycache__" not in str(path):
                    files.append(path)
            if len(files) >= limit:
                break
        return files[:limit]

    # ------------------------------------------------------------ verification
    async def _run_proc(self, cmd: list[str], cwd: Optional[Path] = None, timeout: int = 300) -> tuple[int, str, str]:
        """Run a subprocess (no shell) and return (exit_code, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            code = proc.returncode if proc.returncode is not None else 0
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", f"verification timed out after {timeout}s"
        return code, stdout_bytes.decode("utf-8", errors="replace"), stderr_bytes.decode("utf-8", errors="replace")

    async def _verify_change(self, target: Path) -> tuple[bool, str]:
        """Return (passed, report) — the verification loop for a changed file.

        1. Syntax-check the changed module with `py_compile`.
        2. If pytest is installed, run the full test suite from the project
           root (exit 0 passes; exit 5 = no tests collected is a warning).
        """
        root = self.pipeline.settings.project_root
        checks: list[str] = []

        code, _, err = await self._run_proc([sys.executable, "-m", "py_compile", str(target)], cwd=root, timeout=120)
        if code != 0:
            return False, f"❌ Syntax check failed for {target.name}:\n{(err or 'unknown error').strip()[:1500]}"
        checks.append(f"✔ syntax — {target.name} compiles")

        if importlib.util.find_spec("pytest") is None:
            return True, "\n".join(checks) + "\n⚠ pytest not installed — syntax check only (install dev deps for full loop)"

        code, out, err = await self._run_proc([sys.executable, "-m", "pytest", "-q"], cwd=root, timeout=300)
        if code == PYTEST_NO_TESTS:
            return True, "\n".join(checks) + "\n⚠ pytest ran but collected no tests"
        if code != 0:
            tail = "\n".join((out or err).strip().splitlines()[-25:])
            return False, f"❌ Test suite failed (exit {code}):\n{tail[:2000]}"
        last = next((line for line in reversed((out or "").strip().splitlines()) if line.strip()), "")
        return True, "\n".join(checks) + f"\n✔ tests — {last}"

    # ---------------------------------------------------------------- git snapshots
    async def _git(self, *args: str) -> tuple[int, str, str]:
        """Run a git subcommand in the project root (no shell)."""
        return await self._run_proc(
            ["git", *args],
            cwd=self.pipeline.settings.project_root,
            timeout=60,
        )

    async def _git_is_repo(self) -> bool:
        """Whether the project root is inside a git work tree."""
        try:
            code, _, _ = await self._git("rev-parse", "--is-inside-work-tree")
        except Exception:
            return False
        return code == 0

    def _git_rel(self, target: Path) -> str:
        """POSIX-style path of `target` relative to the project root."""
        return target.relative_to(self.pipeline.settings.project_root).as_posix()

    async def _git_snapshot(self, target: Path) -> Optional[str]:
        """Commit the file's current content; return the snapshot short sha.

        Returns None when the file already matches HEAD (nothing to commit),
        or the string "new" when the file does not exist yet (rollback then
        means deleting it).  Raises on git failure so callers can fall back.
        """
        rel = self._git_rel(target)
        if not target.exists():
            return "new"
        code, _, err = await self._git("add", "--", rel)
        if code != 0:
            raise RuntimeError(f"git add failed: {(err or '').strip()[:200]}")
        code, _, _ = await self._git("diff", "--cached", "--quiet", "--", rel)
        if code == 0:
            return None  # unchanged — nothing to snapshot
        code, _, err = await self._git("commit", "-m", f"self-modify: pre-patch snapshot of {rel}")
        if code != 0:
            raise RuntimeError(f"git commit failed: {(err or '').strip()[:200]}")
        _, out, _ = await self._git("rev-parse", "--short", "HEAD")
        return out.strip()

    async def _git_restore(self, target: Path, existed_before: bool) -> None:
        """Restore the pre-patch state: checkout the file, or delete it if it
        did not exist before the patch."""
        rel = self._git_rel(target)
        if existed_before:
            code, _, err = await self._git("checkout", "--", rel)
            if code != 0:
                raise RuntimeError(f"git checkout failed: {(err or '').strip()[:200]}")
        else:
            target.unlink(missing_ok=True)

    async def _git_commit_patch(self, target: Path, reason: str) -> Optional[str]:
        """Commit the applied patch; return the commit short sha (or None if
        nothing changed)."""
        rel = self._git_rel(target)
        code, _, err = await self._git("add", "--", rel)
        if code != 0:
            raise RuntimeError(f"git add failed: {(err or '').strip()[:200]}")
        code, _, _ = await self._git("diff", "--cached", "--quiet", "--", rel)
        if code == 0:
            return None
        code, _, err = await self._git(
            "commit", "-m", f"self-modify: applied patch to {rel} ({reason[:80]})"
        )
        if code != 0:
            raise RuntimeError(f"git commit failed: {(err or '').strip()[:200]}")
        _, out, _ = await self._git("rev-parse", "--short", "HEAD")
        return out.strip()

    # ---------------------------------------------------------------- api
    async def inspect(self, path: str) -> AgentResult:
        target = self._resolve(path)
        if not target.exists():
            return AgentResult(ok=False, output=f"File not found: {target}", intent="self_improve", error="not found")
        content = await self.pipeline.system_io.read_file(str(target))
        return AgentResult(ok=True, output=f"--- {target} ---\n{content}", intent="self_improve")

    async def verify(self, path: str) -> AgentResult:
        """Run the verification loop against a file without modifying anything."""
        target = self._resolve(path)
        if not target.exists():
            return AgentResult(ok=False, output=f"File not found: {target}", intent="self_improve", error="not found")
        passed, report = await self._verify_change(target)
        status = "PASSED" if passed else "FAILED"
        return AgentResult(
            ok=passed,
            output=f"Verification for {target} — {status}\n{report}",
            intent="self_improve",
            error=None if passed else "verification failed",
        )

    async def suggest(self) -> AgentResult:
        files = self._pick_files()
        if not files:
            return AgentResult(ok=False, output="No source files found to review.", intent="self_improve")
        sections = []
        for path in files:
            try:
                content = await self.pipeline.system_io.read_file(str(path))
            except Exception:
                continue
            sections.append(f"### FILE: {path.relative_to(self.pipeline.settings.project_root)}\n{content[:3000]}")
        if self.pipeline.llm.route() == "none":
            reviewed = "\n".join(str(f) for f in files)
            return AgentResult(
                ok=True,
                output=f"⚠ LLM unavailable — cannot generate suggestions.\nReviewed files:\n{reviewed}",
                intent="self_improve",
            )
        text = await self.pipeline.llm.complete(
            [
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(sections)},
            ],
            temperature=0.4,
            max_tokens=2000,
        )
        self._audit("self_improve.suggested", action="self_modify", detail={"files": [str(f) for f in files]})
        return AgentResult(ok=True, output=text, intent="self_improve")

    async def apply_patch(self, path: str, new_content: str, reason: str = "operator request") -> AgentResult:
        target = self._resolve(path)
        decision = self.pipeline.guardian.guard(
            "self_modify",
            {"path": str(target), "reason": reason},
            actor="agent:self_improve",
        )
        # Decision is guaranteed allowed here (guard raises otherwise).
        root = self.pipeline.settings.project_root
        existed_before = target.exists()
        backup: Optional[Path] = None
        method = "backup"
        snapshot_sha: Optional[str] = None

        # Prefer a git snapshot when the project is a repo; fall back to the
        # classic data/backups/*.bak copy otherwise (e.g. packaged deploys
        # without git).
        use_git = False
        if await self._git_is_repo():
            try:
                snapshot_sha = await self._git_snapshot(target)
                use_git = True
                method = "git"
            except Exception:
                use_git = False  # git failed — fall back to .bak

        if not use_git and existed_before:
            backup = self.pipeline.settings.backups_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{target.name}.bak"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_content, encoding="utf-8")
        self._audit(
            "self_improve.applied",
            action="self_modify",
            detail={
                "path": str(target),
                "reason": reason,
                "method": method,
                "snapshot": snapshot_sha,
                "backup": str(backup) if backup else None,
                "bytes": len(new_content),
            },
        )

        # ---- verification loop: keep the change only if it passes ----------
        passed, report = await self._verify_change(target)
        if not passed:
            rollback_note = ""
            if use_git:
                try:
                    await self._git_restore(target, existed_before)
                    rollback_note = f"restored via git ({snapshot_sha or 'HEAD'})"
                except Exception as exc:
                    rollback_note = f"git restore FAILED — manual intervention required: {exc}"
            elif backup is not None:
                target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
                rollback_note = f"restored from backup ({backup.name})"
            self._audit(
                "self_improve.rolled_back",
                action="self_modify",
                detail={
                    "path": str(target),
                    "reason": reason,
                    "method": method,
                    "snapshot": snapshot_sha,
                    "backup": str(backup) if backup else None,
                    "rollback": rollback_note or "no snapshot available",
                },
            )
            return AgentResult(
                ok=False,
                output=(
                    f"✘ Self-modification to {target} FAILED verification — change rolled back "
                    f"({rollback_note or 'no snapshot available'}).\n\n{report}"
                ),
                intent="self_improve",
                error="verification failed",
            )

        patch_sha = None
        if use_git:
            try:
                patch_sha = await self._git_commit_patch(target, reason)
            except Exception:
                patch_sha = None  # non-fatal — the tree stays modified

        self._audit(
            "self_improve.verified",
            action="self_modify",
            detail={
                "path": str(target),
                "reason": reason,
                "method": method,
                "snapshot": snapshot_sha,
                "patch": patch_sha,
                "backup": str(backup) if backup else None,
            },
        )
        if use_git:
            note = f"\nGit: snapshot {snapshot_sha or '(unchanged)'} → patch {patch_sha or '(uncommitted)'}"
        else:
            note = f"\nBackup: {backup}" if backup else ""
        return AgentResult(
            ok=True,
            output=f"✔ Applied self-modification to {target} — verification passed.\n{report}{note}",
            intent="self_improve",
        )

    # ---------------------------------------------------------------- run
    async def run(self, request: str) -> AgentResult:
        low = request.strip().lower()
        if low.startswith("inspect") or low.startswith("read "):
            path = request.split(" ", 1)[1].strip() if " " in request else ""
            return await self.inspect(path) if path else AgentResult(ok=False, output="Usage: inspect <path>", intent="self_improve")
        if low.startswith("verify"):
            path = request.split(" ", 1)[1].strip() if " " in request else ""
            return await self.verify(path) if path else AgentResult(ok=False, output="Usage: verify <path>", intent="self_improve")
        if low.startswith("apply") or "apply patch" in low:
            return await self._apply_from_text(request)
        if any(w in low for w in ("suggest", "improve", "review", "self")):
            return await self.suggest()
        return await self.suggest()

    async def _apply_from_text(self, text: str) -> AgentResult:
        try:
            spec = json.loads(text[text.find("{"): text.rfind("}") + 1]) if "{" in text else None
        except json.JSONDecodeError:
            spec = None
        if not spec or "path" not in spec or "content" not in spec:
            return AgentResult(
                ok=False,
                output='Usage: apply {"path": "agents/x.py", "content": "<new source>", "reason": "why"}',
                intent="self_improve",
            )
        return await self.apply_patch(str(spec["path"]), str(spec["content"]), str(spec.get("reason", "operator request")))
