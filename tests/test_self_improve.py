"""Tests for the self-improvement verification loop (patch → tests → rollback)."""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

from agents.self_improve import SelfImproveAgent


def _make_agent(tmp_path: Path) -> SelfImproveAgent:
    """Build a SelfImproveAgent with a stub pipeline (no full Pipeline needed)."""
    pipeline = types.SimpleNamespace(
        settings=types.SimpleNamespace(project_root=tmp_path),
    )
    agent = SelfImproveAgent.__new__(SelfImproveAgent)
    agent.pipeline = pipeline
    return agent


def test_verify_change_passes_valid_module(tmp_path, monkeypatch):
    target = tmp_path / "good.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)  # skip pytest branch

    agent = _make_agent(tmp_path)
    passed, report = asyncio.run(agent._verify_change(target))

    assert passed is True
    assert "compiles" in report
    assert "syntax check only" in report


def test_verify_change_fails_on_syntax_error(tmp_path, monkeypatch):
    target = tmp_path / "broken.py"
    target.write_text("def broken(:\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    agent = _make_agent(tmp_path)
    passed, report = asyncio.run(agent._verify_change(target))

    assert passed is False
    assert "Syntax check failed" in report


def test_verify_change_runs_pytest_when_available(tmp_path, monkeypatch):
    target = tmp_path / "good.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())  # pytest "installed"
    calls: dict = {}

    async def fake_run(self, cmd, cwd=None, timeout=300):
        calls["cmd"] = list(cmd)
        if cmd[-1] == str(target):  # the py_compile step
            return 0, "", ""
        return 0, "3 passed in 0.01s", ""  # the pytest step

    monkeypatch.setattr(SelfImproveAgent, "_run_proc", fake_run)

    agent = _make_agent(tmp_path)
    passed, report = asyncio.run(agent._verify_change(target))

    assert passed is True
    assert "3 passed" in report
    assert "pytest" in calls["cmd"]


def test_verify_change_fails_when_pytest_fails(tmp_path, monkeypatch):
    target = tmp_path / "good.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    calls: dict = {}

    async def fake_run(self, cmd, cwd=None, timeout=300):
        calls["cmd"] = list(cmd)
        if cmd[-1] == str(target):
            return 0, "", ""
        return 1, "FAILED tests/test_x.py::test_y\n1 failed", ""

    monkeypatch.setattr(SelfImproveAgent, "_run_proc", fake_run)

    agent = _make_agent(tmp_path)
    passed, report = asyncio.run(agent._verify_change(target))

    assert passed is False
    assert "Test suite failed" in report
    assert "FAILED tests/test_x.py::test_y" in report


def test_resolve_rejects_paths_outside_project(tmp_path):
    agent = _make_agent(tmp_path)
    try:
        agent._resolve(str(Path(sys.prefix) / "outside.py"))
        raised = False
    except ValueError:
        raised = True
    assert raised is True


def test_resolve_accepts_relative_project_path(tmp_path):
    (tmp_path / "agents").mkdir()
    target = tmp_path / "agents" / "x.py"
    target.write_text("x = 1\n", encoding="utf-8")

    agent = _make_agent(tmp_path)
    assert agent._resolve("agents/x.py") == target.resolve()


# ---------------------------------------------------------------- integration
# Building the real Pipeline is safe in tests: no service connects at init
# (MQTT/mediapipe/browser are lazy), and settings can point at a temp root.


def _pipeline(tmp_path):
    from backend.config import Settings
    from agents.router import Pipeline

    pipeline = Pipeline(Settings())
    # Re-home the project tree so path-restricted operations stay in the test
    # temp dir; backups still land in the real data/backups/ (harmless).
    pipeline.settings.project_root = tmp_path
    return pipeline


def test_dispatch_self_modify_returns_pending_consent(tmp_path):
    """HIGH-severity self_modify must surface as pending consent, not a 500."""
    pipeline = _pipeline(tmp_path)
    try:
        pipeline.consent.set_mode("strict")

        async def run():
            async def fake_classify(message):
                return {"intent": "self_improve", "tool": None, "args": {}}

            pipeline.router.classify = fake_classify
            return await pipeline.router.dispatch(
                'apply {"path": "tests/scratch_patch_target.py", "content": "x = 1\\n"}'
            )

        result = asyncio.run(run())
        assert result.pending_consent is not None
        assert result.pending_consent["action"] == "self_modify"
        assert result.error == "consent required"
        assert result.ok is False
    finally:
        asyncio.run(pipeline.close())


def test_apply_patch_verifies_and_rolls_back(tmp_path):
    """Good patch lands; broken patch fails verification and is rolled back."""
    pipeline = _pipeline(tmp_path)
    try:
        pipeline.consent.set_mode("auto")  # skip the consent round-trip

        async def run():
            good = await pipeline.self_improve.apply_patch("mod.py", "x = 1\n", reason="test")
            after_good = (tmp_path / "mod.py").read_text(encoding="utf-8")
            bad = await pipeline.self_improve.apply_patch("mod.py", "def broken(:\n", reason="test")
            after_rollback = (tmp_path / "mod.py").read_text(encoding="utf-8")
            return good, after_good, bad, after_rollback

        good, after_good, bad, after_rollback = asyncio.run(run())
        assert good.ok is True
        assert "verification passed" in good.output
        assert after_good == "x = 1\n"
        assert bad.ok is False
        assert bad.error == "verification failed"
        assert "rolled back" in bad.output
        assert after_rollback == "x = 1\n"  # restored from the pre-write backup
    finally:
        asyncio.run(pipeline.close())


def test_keyword_intent_routes_self_improve_commands_without_llm():
    """apply/verify/inspect must route deterministically, never via the LLM."""
    from agents.router import keyword_intent

    assert keyword_intent('apply {"path": "agents/x.py", "content": "x = 1\\n"}') == "self_improve"
    assert keyword_intent("verify agents/router.py") == "self_improve"
    assert keyword_intent("inspect agents/base.py") == "self_improve"
    assert keyword_intent("review your own code") == "self_improve"


# ---------------------------------------------------------------- git snapshots

def _init_git_repo(tmp_path: Path, monkeypatch, content: str = "x = 0\n") -> None:
    """Create a git repo in tmp_path with a baseline commit and a hermetic identity."""
    import subprocess

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(name, "Test Runner")
    for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, "runner@example.invalid")
    (tmp_path / "mod.py").write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)


def test_apply_patch_snapshots_and_rolls_back_via_git(tmp_path, monkeypatch):
    """In a git repo: patch → verify → commit on success; checkout on failure."""
    import subprocess

    _init_git_repo(tmp_path, monkeypatch)
    # Simulate an external uncommitted edit so the pre-patch snapshot commit
    # has something real to capture (a clean tree is never snapshot-committed).
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    pipeline = _pipeline(tmp_path)
    try:
        pipeline.consent.set_mode("auto")

        async def fake_verify(self, target):
            content = target.read_text(encoding="utf-8")
            return (content.startswith("x ="), "fake report")

        monkeypatch.setattr(SelfImproveAgent, "_verify_change", fake_verify)

        async def run():
            good = await pipeline.self_improve.apply_patch("mod.py", "x = 2\n", reason="test")
            bad = await pipeline.self_improve.apply_patch("mod.py", "def broken(:\n", reason="test")
            return good, bad

        good, bad = asyncio.run(run())
        assert good.ok is True
        assert "Git: snapshot" in good.output
        assert (tmp_path / "mod.py").read_text(encoding="utf-8") == "x = 2\n"
        assert bad.ok is False
        assert "rolled back" in bad.output
        assert (tmp_path / "mod.py").read_text(encoding="utf-8") == "x = 2\n", "git checkout must restore the snapshot"

        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
        ).stdout.strip().splitlines()
        assert len(log) == 3, f"expected baseline+snapshot+patch, got {log}"
        assert log[0].endswith("self-modify: applied patch to mod.py (test)")
        assert log[1].endswith("self-modify: pre-patch snapshot of mod.py")
        assert log[2].endswith("baseline")
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True
        ).stdout
        assert "mod.py" not in status, "working tree should be clean after rollback"
    finally:
        asyncio.run(pipeline.close())


def test_apply_patch_falls_back_to_backup_without_git(tmp_path, monkeypatch):
    """Outside a git repo, self-modify must keep using .bak snapshots."""
    # Pre-create the file so the good patch has something to snapshot.
    (tmp_path / "mod.py").write_text("x = 0\n", encoding="utf-8")
    pipeline = _pipeline(tmp_path)
    try:
        pipeline.consent.set_mode("auto")

        async def fake_verify(self, target):
            content = target.read_text(encoding="utf-8")
            return (content.startswith("x ="), "fake report")

        monkeypatch.setattr(SelfImproveAgent, "_verify_change", fake_verify)

        async def run():
            good = await pipeline.self_improve.apply_patch("mod.py", "x = 1\n", reason="test")
            bad = await pipeline.self_improve.apply_patch("mod.py", "def broken(:\n", reason="test")
            return good, bad

        good, bad = asyncio.run(run())
        assert good.ok is True
        assert "Backup:" in good.output, "non-git environments must fall back to .bak snapshots"
        assert (tmp_path / "mod.py").read_text(encoding="utf-8") == "x = 1\n"
        assert bad.ok is False and "rolled back" in bad.output
        assert (tmp_path / "mod.py").read_text(encoding="utf-8") == "x = 1\n"
    finally:
        asyncio.run(pipeline.close())
