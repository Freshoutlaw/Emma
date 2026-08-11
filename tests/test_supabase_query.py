"""Tests for the read-only Supabase query agent — SQL validation and skeleton.

These tests cover the offline path (no DSN configured) and the SQL validator
which can be fully exercised without a live database connection.
"""

import asyncio
import types

import pytest

from agents.supabase_query import SupabaseQueryAgent, validate_sql


# ------------------------------------------------------------------- validators

def test_validate_select_simple():
    ok, reason = validate_sql("SELECT * FROM users")
    assert ok is True
    assert reason == "ok"


def test_validate_with_cte():
    ok, _ = validate_sql("WITH cte AS (SELECT id FROM users) SELECT * FROM cte")
    assert ok is True


def test_validate_select_lowercase():
    ok, _ = validate_sql("select count(*) from episodes")
    assert ok is True


def test_validate_empty_rejected():
    ok, reason = validate_sql("")
    assert ok is False
    assert "empty" in reason


def test_validate_whitespace_only_rejected():
    ok, reason = validate_sql("   \n  ")
    assert ok is False


def test_validate_insert_rejected():
    ok, reason = validate_sql("INSERT INTO users (name) VALUES ('x')")
    assert ok is False
    assert "mutating" in reason


def test_validate_delete_rejected():
    ok, reason = validate_sql("DELETE FROM users WHERE id = 1")
    assert ok is False
    assert "mutating" in reason


def test_validate_drop_rejected():
    ok, reason = validate_sql("DROP TABLE users")
    assert ok is False
    assert "mutating" in reason


def test_validate_update_rejected():
    ok, reason = validate_sql("UPDATE users SET name = 'x'")
    assert ok is False


def test_validate_create_rejected():
    ok, reason = validate_sql("CREATE TABLE foo (id int)")
    assert ok is False


def test_validate_grant_rejected():
    ok, reason = validate_sql("GRANT SELECT ON users TO reader")
    assert ok is False


def test_validate_begin_rejected():
    ok, reason = validate_sql("BEGIN; SELECT 1; COMMIT;")
    assert ok is False


def test_validate_starts_with_from_rejected():
    ok, reason = validate_sql("FROM users SELECT *")
    assert ok is False
    assert "WITH or SELECT" in reason


def test_validate_with_insert_inside_cte_rejected():
    ok, reason = validate_sql("WITH cte AS (INSERT INTO x VALUES (1)) SELECT * FROM cte")
    assert ok is False


def test_validate_with_mixed_case_insert_rejected():
    ok, reason = validate_sql("Select 1; InSeRt INTO x VALUES (1)")
    assert ok is False


# ------------------------------------------------------------------- agent skeleton

def _agent(dsn: str | None = None) -> SupabaseQueryAgent:
    pipeline = types.SimpleNamespace(
        settings=types.SimpleNamespace(supabase_query_dsn=dsn),
        audit=types.SimpleNamespace(log=lambda *a, **k: None),
    )
    agent = SupabaseQueryAgent.__new__(SupabaseQueryAgent)
    agent.pipeline = pipeline
    agent._dsn = dsn
    agent._pool = None
    return agent


def test_not_configured_returns_clear_error():
    agent = _agent(dsn=None)
    assert agent.is_configured() is False
    result = asyncio.run(agent.query("SELECT 1"))
    assert result.ok is False
    assert "not configured" in result.error
    assert "EMMA_SUPABASE_QUERY_DSN" in result.output


def test_not_configured_list_tables():
    agent = _agent(dsn=None)
    result = asyncio.run(agent.list_tables())
    assert result.ok is False
    assert "not configured" in result.error


def test_not_configured_describe_table():
    agent = _agent(dsn=None)
    result = asyncio.run(agent.describe_table("users"))
    assert result.ok is False


def test_rejects_invalid_sql_before_connecting():
    agent = _agent(dsn=None)
    result = asyncio.run(agent.query("DROP TABLE users"))
    assert result.ok is False
    assert "validation failed" in result.error
    assert "mutating" in result.output


def test_rejects_empty_query():
    agent = _agent(dsn=None)
    result = asyncio.run(agent.query(""))
    assert result.ok is False
    assert "empty" in result.output


def test_run_list_tables_recognised():
    agent = _agent(dsn=None)
    result = asyncio.run(agent.run("list tables"))
    assert result.ok is False  # not configured, but route was correct
    assert "not configured" in result.error


def test_run_describe_recognised():
    agent = _agent(dsn=None)
    result = asyncio.run(agent.run("describe users"))
    assert result.ok is False
    assert "not configured" in result.error


def test_run_query_prefix_recognised():
    agent = _agent(dsn=None)
    result = asyncio.run(agent.run("query SELECT 1"))
    assert result.ok is False
    assert "not configured" in result.error


def test_run_rejects_mutating_before_connecting():
    agent = _agent(dsn=None)
    result = asyncio.run(agent.run("run INSERT INTO x VALUES (1)"))
    assert result.ok is False
    assert "mutating" in result.output


def test_agent_has_empty_tool_allowlist():
    """The query agent uses its own pool, not the ControlAgent catalog."""
    agent = _agent(dsn=None)
    assert agent.tool_allowlist == frozenset()
