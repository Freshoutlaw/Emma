"""Tests for the orchestration layer (Tiers 1, 3, 5, 6)."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the project root is on sys.path.
_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)


# =====================================================================
# Tier 1 — Routing Policy
# =====================================================================

class TestRoutingPolicy:
    def test_detect_decomposition_single(self):
        from orchestration.routing_policy import detect_decomposition
        parts = detect_decomposition("list the files in /tmp")
        assert parts == ["list the files in /tmp"]

    def test_detect_decomposition_two_parts(self):
        from orchestration.routing_policy import detect_decomposition
        parts = detect_decomposition("remember this fact and also list the files")
        assert len(parts) == 2
        assert "remember" in parts[0].lower()
        assert "list" in parts[1].lower()

    def test_detect_decomposition_semicolon(self):
        from orchestration.routing_policy import detect_decomposition
        parts = detect_decomposition("show weather, also show the map")
        assert len(parts) == 2

    def test_detect_decomposition_plus(self):
        from orchestration.routing_policy import detect_decomposition
        parts = detect_decomposition("run git status plus show memory")
        assert len(parts) == 2

    def test_priority_tiebreak_security_wins(self):
        from orchestration.routing_policy import priority_tiebreak
        result = priority_tiebreak(["memory", "security", "control"])
        assert result == "security"

    def test_priority_tiebreak_control_beats_reasoning(self):
        from orchestration.routing_policy import priority_tiebreak
        result = priority_tiebreak(["reasoning", "control"])
        assert result == "control"

    def test_maybe_clarify_keyword_decisive(self):
        from orchestration.routing_policy import maybe_clarify
        result = maybe_clarify("remember this", "memory", None, None)
        assert result is None

    def test_maybe_clarify_ambiguous_short(self):
        from orchestration.routing_policy import maybe_clarify
        result = maybe_clarify("show it", "reasoning", "control", 0.3)
        assert result is not None
        assert "clarify" in result.lower()

    def test_maybe_clarify_llm_confident(self):
        from orchestration.routing_policy import maybe_clarify
        result = maybe_clarify("complex question", "reasoning", "reasoning", 0.9)
        assert result is None

    def test_apply_policy_keyword_wins(self):
        from orchestration.routing_policy import apply_policy
        decision = apply_policy("remember this fact", "memory")
        assert decision.intents == ["memory"]
        assert not decision.decomposed
        # Keyword matched (not "reasoning"), no LLM — goes to default path.
        assert decision.reason in ("keyword_priority", "default")

    def test_apply_policy_llm_resolves(self):
        from orchestration.routing_policy import apply_policy
        decision = apply_policy("complex multi-step task", "reasoning", {"intent": "control", "confidence": 0.8})
        assert decision.intents == ["control"]
        assert decision.reason == "llm_resolved"

    def test_apply_policy_decomposition(self):
        from orchestration.routing_policy import apply_policy
        decision = apply_policy("remember X and also list files", "reasoning")
        assert decision.decomposed
        assert len(decision.messages) == 2

    def test_apply_policy_clarify(self):
        from orchestration.routing_policy import apply_policy
        decision = apply_policy("show it", "reasoning", {"intent": "control", "confidence": 0.2})
        assert decision.clarify is not None

    def test_quick_keyword_memory(self):
        from orchestration.routing_policy import _quick_keyword
        assert _quick_keyword("remember this fact") == "memory"

    def test_quick_keyword_security(self):
        from orchestration.routing_policy import _quick_keyword
        assert _quick_keyword("engage kill switch") == "security"

    def test_quick_keyword_control(self):
        from orchestration.routing_policy import _quick_keyword
        assert _quick_keyword("run ls -la") == "control"

    def test_quick_keyword_default(self):
        from orchestration.routing_policy import _quick_keyword
        assert _quick_keyword("what is the meaning of life") == "reasoning"


# =====================================================================
# Tier 3 — Failure Isolation
# =====================================================================

class TestFailureIsolation:
    def test_fresh_agent_can_be_called(self):
        from orchestration.failure_isolation import FailureIsolation
        fi = FailureIsolation()
        allowed, _ = fi.can_call("test_agent")
        assert allowed

    def test_success_resets_count(self):
        from orchestration.failure_isolation import FailureIsolation
        fi = FailureIsolation()
        for _ in range(4):
            fi.record_failure("agent_x")
        fi.record_success("agent_x")
        allowed, _ = fi.can_call("agent_x")
        assert allowed

    def test_circuit_opens_after_max_failures(self):
        from orchestration.failure_isolation import FailureIsolation, CircuitState
        fi = FailureIsolation()
        for _ in range(5):
            fi.record_failure("flaky_agent")
        circuit = fi._circuit("flaky_agent")
        assert circuit.state == CircuitState.OPEN

    def test_open_circuit_rejects(self):
        from orchestration.failure_isolation import FailureIsolation, CircuitState
        fi = FailureIsolation()
        for _ in range(5):
            fi.record_failure("flaky_agent")
        allowed, reason = fi.can_call("flaky_agent")
        assert not allowed
        assert "OPEN" in reason
        fi.reject("flaky_agent")

    def test_half_open_after_cooldown(self):
        from orchestration.failure_isolation import FailureIsolation, CircuitState, AGENT_BUDGETS
        fi = FailureIsolation()
        # Use a custom short cooldown.
        from orchestration.failure_isolation import FailureBudget
        AGENT_BUDGETS["quick_agent"] = FailureBudget(max_failures=2, window_seconds=10, cooldown_seconds=0.01)
        for _ in range(2):
            fi.record_failure("quick_agent")
        assert fi._circuit("quick_agent").state == CircuitState.OPEN
        time.sleep(0.02)
        allowed, _ = fi.can_call("quick_agent")
        assert allowed
        assert fi._circuit("quick_agent").state == CircuitState.HALF_OPEN
        del AGENT_BUDGETS["quick_agent"]

    def test_probe_success_closes_circuit(self):
        from orchestration.failure_isolation import FailureIsolation, CircuitState, AGENT_BUDGETS
        fi = FailureIsolation()
        from orchestration.failure_isolation import FailureBudget
        AGENT_BUDGETS["probe_agent"] = FailureBudget(max_failures=2, window_seconds=10, cooldown_seconds=0.01)
        for _ in range(2):
            fi.record_failure("probe_agent")
        time.sleep(0.02)
        fi.can_call("probe_agent")  # transitions to HALF_OPEN
        fi.record_success("probe_agent")
        assert fi._circuit("probe_agent").state == CircuitState.CLOSED
        del AGENT_BUDGETS["probe_agent"]

    def test_probe_failure_reopens_circuit(self):
        from orchestration.failure_isolation import FailureIsolation, CircuitState, AGENT_BUDGETS
        fi = FailureIsolation()
        from orchestration.failure_isolation import FailureBudget
        AGENT_BUDGETS["probe2_agent"] = FailureBudget(max_failures=2, window_seconds=10, cooldown_seconds=0.01)
        for _ in range(2):
            fi.record_failure("probe2_agent")
        time.sleep(0.02)
        fi.can_call("probe2_agent")  # HALF_OPEN
        fi.record_failure("probe2_agent")
        assert fi._circuit("probe2_agent").state == CircuitState.OPEN
        del AGENT_BUDGETS["probe2_agent"]

    def test_status_returns_all_tracked(self):
        from orchestration.failure_isolation import FailureIsolation
        fi = FailureIsolation()
        fi.record_success("a")
        fi.record_failure("b")
        status = fi.status()
        assert "a" in status
        assert "b" in status
        assert status["a"]["state"] == "closed"
        assert status["b"]["total_failures"] == 1

    def test_reset_clears_specific(self):
        from orchestration.failure_isolation import FailureIsolation
        fi = FailureIsolation()
        fi.record_failure("x")
        fi.record_failure("y")
        fi.reset("x")
        assert fi.get("x") is None if hasattr(fi, "get") else "x" not in fi._circuits

    def test_reset_clears_all(self):
        from orchestration.failure_isolation import FailureIsolation
        fi = FailureIsolation()
        fi.record_failure("a")
        fi.record_failure("b")
        fi.reset()
        assert len(fi._circuits) == 0


# =====================================================================
# Tier 5 — Handoff System
# =====================================================================

class TestHandoffSystem:
    def test_create_handoff(self):
        from orchestration.handoff import create_handoff
        p = create_handoff("reasoning", "control", "Run the next step", "run git status")
        assert p.source_agent == "reasoning"
        assert p.target_agent == "control"
        assert p.description == "Run the next step"
        assert len(p.id) == 12

    def test_propose_and_approve(self):
        from orchestration.handoff import HandoffManager, create_handoff
        mgr = HandoffManager()
        p = create_handoff("a", "b", "do something", "do it")
        mgr.propose(p)
        assert len(mgr.pending()) == 1
        approved = mgr.approve(p.id)
        assert approved is not None
        assert approved.target_agent == "b"
        assert len(mgr.pending()) == 0

    def test_approve_expired_returns_none(self):
        from orchestration.handoff import HandoffManager, HandoffProposal
        mgr = HandoffManager()
        p = HandoffProposal(
            source_agent="a", target_agent="b",
            description="test", message="test",
            created_at=time.monotonic() - 1000,
            ttl_seconds=1,
        )
        mgr.propose(p)
        result = mgr.approve(p.id)
        assert result is None

    def test_deny(self):
        from orchestration.handoff import HandoffManager, create_handoff
        mgr = HandoffManager()
        p = create_handoff("a", "b", "desc", "msg")
        mgr.propose(p)
        assert mgr.deny(p.id) is True
        assert len(mgr.pending()) == 0

    def test_deny_nonexistent(self):
        from orchestration.handoff import HandoffManager
        mgr = HandoffManager()
        assert mgr.deny("nonexistent") is False

    def test_history_recorded(self):
        from orchestration.handoff import HandoffManager, create_handoff
        mgr = HandoffManager()
        p = create_handoff("a", "b", "desc", "msg")
        mgr.propose(p)
        mgr.approve(p.id)
        history = mgr.history()
        assert len(history) == 1
        assert history[0]["outcome"] == "approved"

    def test_pending_cleans_expired(self):
        from orchestration.handoff import HandoffManager, HandoffProposal, create_handoff
        mgr = HandoffManager()
        p1 = HandoffProposal(
            source_agent="a", target_agent="b",
            description="old", message="old",
            created_at=time.monotonic() - 1000,
            ttl_seconds=1,
        )
        p2 = create_handoff("a", "b", "new", "new")
        mgr.propose(p1)
        mgr.propose(p2)
        pending = mgr.pending()
        assert len(pending) == 1
        assert pending[0]["id"] == p2.id


# =====================================================================
# Tier 6 — Agent Registry
# =====================================================================

class TestAgentRegistry:
    def test_register_builtin(self):
        from orchestration.agent_registry import AgentRegistry
        reg = AgentRegistry()
        reg.register_builtin("control", "Executes tools")
        manifest = reg.get("control")
        assert manifest is not None
        assert manifest.class_path == "builtin"

    def test_builtin_cannot_be_overridden(self):
        from orchestration.agent_registry import AgentRegistry, AgentManifest
        reg = AgentRegistry()
        reg.register_builtin("control", "Executes tools")
        # Try to override via manifest.
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "control.yaml"
            manifest_path.write_text("name: control\nclass: fake.Module\n")
            reg.load_manifests(Path(tmpdir))
        assert reg.get("control").class_path == "builtin"

    def test_load_manifests(self):
        from orchestration.agent_registry import AgentRegistry
        reg = AgentRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "research.yaml"
            mp.write_text("name: research\nclass: agents.research.ResearchAgent\ndescription: Does research\ntags:\n  - data\n")
            count = reg.load_manifests(Path(tmpdir))
        assert count == 1
        manifest = reg.get("research")
        assert manifest is not None
        assert manifest.class_path == "agents.research.ResearchAgent"
        assert "data" in manifest.tags

    def test_load_manifests_yml(self):
        from orchestration.agent_registry import AgentRegistry
        reg = AgentRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "custom.yml"
            mp.write_text("name: custom\nclass: agents.custom.CustomAgent\n")
            count = reg.load_manifests(Path(tmpdir))
        assert count == 1

    def test_invalid_manifest_skipped(self):
        from orchestration.agent_registry import AgentRegistry
        reg = AgentRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "bad.yaml"
            mp.write_text("name: bad\n")  # missing class
            count = reg.load_manifests(Path(tmpdir))
        assert count == 0

    def test_by_tag(self):
        from orchestration.agent_registry import AgentRegistry
        reg = AgentRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "t1.yaml"
            mp.write_text("name: agent1\nclass: a.b.C\ntags:\n  - research\n  - data\n")
            mp2 = Path(tmpdir) / "t2.yaml"
            mp2.write_text("name: agent2\nclass: a.b.D\ntags:\n  - design\n")
            reg.load_manifests(Path(tmpdir))
        results = reg.by_tag("research")
        assert len(results) == 1
        assert results[0].name == "agent1"

    def test_handoff_targets(self):
        from orchestration.agent_registry import AgentRegistry
        reg = AgentRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "h.yaml"
            mp.write_text("name: agent_h\nclass: a.b.C\nhandoff_to:\n  - reasoning\n  - control\n")
            reg.load_manifests(Path(tmpdir))
        targets = reg.handoff_targets("agent_h")
        assert "reasoning" in targets
        assert "control" in targets

    def test_handoff_targets_unknown(self):
        from orchestration.agent_registry import AgentRegistry
        reg = AgentRegistry()
        assert reg.handoff_targets("nonexistent") == []

    def test_start_stop_watching(self):
        from orchestration.agent_registry import AgentRegistry
        reg = AgentRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            reg.start_watching(Path(tmpdir), interval=0.1)
            assert reg._watch_thread is not None
            assert reg._watch_thread.is_alive()
            reg.stop_watching()
            assert reg._watch_thread is None

    def test_all_agents(self):
        from orchestration.agent_registry import AgentRegistry
        reg = AgentRegistry()
        reg.register_builtin("a", "Agent A")
        reg.register_builtin("b", "Agent B")
        all_agents = reg.all_agents()
        assert len(all_agents) == 2
        assert "a" in all_agents
        assert "b" in all_agents

    def test_parse_yaml_fallback(self):
        """Test the minimal YAML parser (no PyYAML)."""
        from orchestration.agent_registry import AgentRegistry
        reg = AgentRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "fb.yaml"
            mp.write_text('name: fallback_agent\nclass: agents.fb.Agent\ndescription: "Test"\ntool_allowlist:\n  - read_file\n  - list_dir\ntags:\n  - test\n')
            reg.load_manifests(Path(tmpdir))
        manifest = reg.get("fallback_agent")
        assert manifest is not None
        assert manifest.tool_allowlist == ["read_file", "list_dir"]
        assert "test" in manifest.tags


# =====================================================================
# Integration: AgentResult handoff field
# =====================================================================

class TestAgentResultHandoff:
    def test_handoff_none_by_default(self):
        from agents.base import AgentResult
        r = AgentResult(ok=True, output="test")
        assert r.handoff is None
        d = r.to_dict()
        assert "handoff" not in d

    def test_handoff_included_in_to_dict(self):
        from agents.base import AgentResult
        from orchestration.handoff import create_handoff
        r = AgentResult(ok=True, output="test")
        r.handoff = create_handoff("a", "b", "desc", "msg")
        d = r.to_dict()
        assert "handoff" in d
        assert d["handoff"]["source_agent"] == "a"
        assert d["handoff"]["target_agent"] == "b"
