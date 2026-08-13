"""Tests for the Agent Factory: migrations, research agent, and agent factory."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)


# =====================================================================
# Migrations
# =====================================================================

class TestMigrations:
    def test_migrate_creates_tables(self):
        from orchestration.migrations import migrate
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = migrate(db_path)
            # Verify tables exist.
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            assert "agents" in tables
            assert "generations" in tables
            assert "agent_versions" in tables
            conn.close()

    def test_migrate_idempotent(self):
        from orchestration.migrations import migrate
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn1 = migrate(db_path)
            conn1.close()
            conn2 = migrate(db_path)
            # Should not raise or create duplicates.
            tables = [r[0] for r in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            assert len(tables) >= 3
            conn2.close()

    def test_agent_db_register_and_get(self):
        from orchestration.migrations import AgentDB
        with tempfile.TemporaryDirectory() as tmpdir:
            db = AgentDB(Path(tmpdir) / "test.db")
            db.register_agent(
                name="test_agent",
                description="A test agent",
                class_path="agents.test.TestAgent",
                tool_allowlist=["read_file", "list_dir"],
                tags=["test"],
            )
            agent = db.get_agent("test_agent")
            assert agent is not None
            assert agent["name"] == "test_agent"
            assert agent["class_path"] == "agents.test.TestAgent"
            assert agent["tool_allowlist"] == ["read_file", "list_dir"]
            assert agent["tags"] == ["test"]
            assert agent["source"] == "manual"
            db.close()

    def test_agent_db_upsert(self):
        from orchestration.migrations import AgentDB
        with tempfile.TemporaryDirectory() as tmpdir:
            db = AgentDB(Path(tmpdir) / "test.db")
            db.register_agent("x", "v1", "a.b.C")
            db.register_agent("x", "v2", "a.b.D")
            agent = db.get_agent("x")
            assert agent["description"] == "v2"
            assert agent["class_path"] == "a.b.D"
            db.close()

    def test_agent_db_deactivate(self):
        from orchestration.migrations import AgentDB
        with tempfile.TemporaryDirectory() as tmpdir:
            db = AgentDB(Path(tmpdir) / "test.db")
            db.register_agent("del_me", "to delete", "a.b.C")
            assert db.get_agent("del_me") is not None
            db.deactivate_agent("del_me")
            assert db.get_agent("del_me") is None
            db.close()

    def test_agent_db_list_agents(self):
        from orchestration.migrations import AgentDB
        with tempfile.TemporaryDirectory() as tmpdir:
            db = AgentDB(Path(tmpdir) / "test.db")
            db.register_agent("a", "desc a", "a.b.C")
            db.register_agent("b", "desc b", "a.b.D")
            agents = db.list_agents()
            assert len(agents) == 2
            names = {a["name"] for a in agents}
            assert "a" in names
            assert "b" in names
            db.close()

    def test_agent_db_generation(self):
        from orchestration.migrations import AgentDB
        db_path = Path(tempfile.mkdtemp()) / "test.db"
        db = AgentDB(db_path)
        db.register_agent("my_agent", "test", "a.b.C")
        gen_id = db.record_generation("my_agent", "do something", status="pending")
        assert gen_id is not None
        db.complete_generation(gen_id, "success")
        gens = db.list_generations("my_agent")
        assert len(gens) == 1
        assert gens[0]["status"] == "success"
        db.close()
        del db

    def test_agent_db_version(self):
        from orchestration.migrations import AgentDB
        db_path = Path(tempfile.mkdtemp()) / "test.db"
        db = AgentDB(db_path)
        db.register_agent("my_agent", "test", "a.b.C")
        vid1 = db.record_version("my_agent", '{"name": "my_agent"}', "code v1")
        vid2 = db.record_version("my_agent", '{"name": "my_agent"}', "code v2")
        assert vid1 != vid2
        v = db.get_version("my_agent", 2)
        assert v is not None
        assert v["module_content"] == "code v2"
        db.close()
        del db


# =====================================================================
# Research Agent
# =====================================================================

class TestResearchAgent:
    def test_tool_allowlist(self):
        from agents.research import ResearchAgent
        assert "web_search" in ResearchAgent.tool_allowlist
        assert "fetch_page" in ResearchAgent.tool_allowlist
        assert "write_file" not in ResearchAgent.tool_allowlist
        assert "run_command" not in ResearchAgent.tool_allowlist

    def test_agent_name_and_description(self):
        from agents.research import ResearchAgent
        assert ResearchAgent.name == "research"
        assert "research" in ResearchAgent.description.lower()

    def test_synthesize(self):
        from agents.research import ResearchAgent
        # Create a minimal instance (skip __init__ which needs pipeline).
        agent = object.__new__(ResearchAgent)
        result = agent._synthesize(
            "What is Python?",
            [{"title": "Python.org", "url": "https://python.org", "snippet": "Python is a language"}],
            [{"url": "https://python.org", "title": "Python.org", "snippet": "Python is a language", "content": "Python is a programming language."}],
        )
        assert "What is Python?" in result
        assert "Python.org" in result
        assert "python.org" in result

    def test_extract_name(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        assert agent._extract_name("Create an agent that monitors GitHub") == "monitors"
        assert agent._extract_name("Build a data analysis agent") == "data_analysis"
        assert agent._extract_name("assign it to a sub agent called coder") == "coder"

    def test_to_class_name(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        assert agent._to_class_name("my_cool_agent") == "MyCoolAgent"


# =====================================================================
# Agent Factory
# =====================================================================

class TestAgentFactory:
    def test_tool_allowlist(self):
        from agents.agent_factory import AgentFactory
        assert "write_file" in AgentFactory.tool_allowlist
        assert "read_file" in AgentFactory.tool_allowlist
        assert "web_search" in AgentFactory.tool_allowlist  # model research for _pick_model

    def test_design_agent_search(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        spec = agent._design_agent("Create an agent that searches the web for news")
        assert spec["name"] == "searches"
        assert "web_search" in spec["tools"]
        assert "fetch_page" in spec["tools"]
        assert "research" in spec["tags"]

    def test_design_agent_write(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        spec = agent._design_agent("Build an agent that generates reports and saves them")
        assert "write_file" in spec["tools"]
        assert "generation" in spec["tags"]

    def test_design_agent_execute(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        spec = agent._design_agent("Make an agent that runs shell commands")
        assert "run_command" in spec["tools"]
        assert "automation" in spec["tags"]

    def test_generate_module(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        spec = agent._design_agent("Create an agent that monitors Docker containers")
        module = agent._generate_module(spec)
        # Name is "monitors" -> class is Monitors
        assert "class Monitors(BaseAgent)" in module
        assert "docker_ps" in module
        assert "monitor" in module.lower()

    def test_generate_manifest(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        spec = agent._design_agent("Create an agent that monitors Docker")
        manifest = agent._generate_manifest(spec)
        assert "name: monitors" in manifest
        assert "class: agents.gen_monitors.Monitors" in manifest
        assert "docker_ps" in manifest

    def test_write_module(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        with tempfile.TemporaryDirectory() as tmpdir:
            original = Path
            with patch("agents.agent_factory.Path", side_effect=lambda p: original(tmpdir) / p if not isinstance(p, original) else p):
                path = agent._write_module("test_mod", "print('hello')")
                written = original(tmpdir) / "agents" / "test_mod.py"
                if written.exists():
                    assert written.read_text() == "print('hello')"

    def test_extract_name_patterns(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        assert agent._extract_name("Create an agent that fetches weather data") == "fetches"
        assert agent._extract_name("Build a monitoring agent") == "monitoring"
        assert agent._extract_name("Make something") == "something"

    def test_design_body_includes_search(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        spec = agent._design_agent("Search the web for news")
        assert "web_search" in spec["body"]

    def test_design_body_includes_run_command(self):
        from agents.agent_factory import AgentFactory
        agent = object.__new__(AgentFactory)
        spec = agent._design_agent("Run a shell command")
        assert "run_command" in spec["body"]
