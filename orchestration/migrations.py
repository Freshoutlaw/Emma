"""Agent Factory — SQLite migration system.

Lightweight schema versioning for the agent database. Each migration is
a named function that runs DDL inside a transaction. The ``migrate()``
function checks the current schema version and runs any pending
migrations in order.

Tables:
  - ``agents``: registered agent configurations (manifest + metadata)
  - ``generations``: history of agent generation attempts by the factory
  - ``agent_versions``: version history for each agent (for rollback)
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Current schema version — bump when adding a migration.
SCHEMA_VERSION = 1

# Migration registry — each entry is (version, name, sql).
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "create_agents", """
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            class_path TEXT NOT NULL,
            tool_allowlist TEXT,          -- JSON array or NULL
            max_plan_steps INTEGER,
            handoff_to TEXT,              -- JSON array or NULL
            tags TEXT,                    -- JSON array or NULL
            manifest_path TEXT,           -- path to YAML file
            source TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'factory' | 'imported'
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """),
    (1, "create_generations", """
        CREATE TABLE IF NOT EXISTS generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            task_description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending | success | failed
            manifest_json TEXT,           -- generated manifest
            module_path TEXT,             -- path to generated .py
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (agent_name) REFERENCES agents(name)
        )
    """),
    (1, "create_agent_versions", """
        CREATE TABLE IF NOT EXISTS agent_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            manifest_json TEXT NOT NULL,
            module_content TEXT,          -- snapshot of generated code
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (agent_name) REFERENCES agents(name)
        )
    """),
    (1, "create_indexes", """
        CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name);
        CREATE INDEX IF NOT EXISTS idx_agents_active ON agents(is_active);
        CREATE INDEX IF NOT EXISTS idx_generations_agent ON generations(agent_name);
        CREATE INDEX IF NOT EXISTS idx_generations_status ON generations(status);
        CREATE INDEX IF NOT EXISTS idx_versions_agent ON agent_versions(agent_name, version);
    """),
]


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or 0 if not initialized."""
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Set the schema version via PRAGMA user_version."""
    conn.execute(f"PRAGMA user_version = {version}")


def migrate(db_path: Path) -> sqlite3.Connection:
    """Open the database and run any pending migrations.

    Returns the connection (caller owns it).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    current = get_schema_version(conn)
    if current >= SCHEMA_VERSION:
        logger.debug("Schema version %d — no migrations needed.", current)
        return conn

    # Run pending migrations in order.
    for version, name, sql in MIGRATIONS:
        if version > current:
            logger.info("Running migration %d: %s", version, name)
            conn.executescript(sql)

    set_schema_version(conn, SCHEMA_VERSION)
    conn.commit()
    logger.info("Schema migrated to version %d.", SCHEMA_VERSION)
    return conn


class AgentDB:
    """High-level interface to the agent database."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = migrate(self._db_path)
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ---------------------------------------------------------------- agents
    def register_agent(
        self,
        name: str,
        description: str,
        class_path: str,
        tool_allowlist: list[str] | None = None,
        max_plan_steps: int | None = None,
        handoff_to: list[str] | None = None,
        tags: list[str] | None = None,
        manifest_path: str | None = None,
        source: str = "manual",
    ) -> None:
        """Register or update an agent in the database."""
        import json
        self.conn.execute(
            """INSERT INTO agents (name, description, class_path, tool_allowlist,
               max_plan_steps, handoff_to, tags, manifest_path, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 description=excluded.description,
                 class_path=excluded.class_path,
                 tool_allowlist=excluded.tool_allowlist,
                 max_plan_steps=excluded.max_plan_steps,
                 handoff_to=excluded.handoff_to,
                 tags=excluded.tags,
                 manifest_path=excluded.manifest_path,
                 source=excluded.source,
                 updated_at=datetime('now')
            """,
            (
                name, description, class_path,
                json.dumps(tool_allowlist) if tool_allowlist else None,
                max_plan_steps,
                json.dumps(handoff_to) if handoff_to else None,
                json.dumps(tags) if tags else None,
                manifest_path, source,
            ),
        )
        self.conn.commit()

    def get_agent(self, name: str) -> dict[str, Any] | None:
        """Get an agent by name."""
        import json
        row = self.conn.execute(
            "SELECT * FROM agents WHERE name = ? AND is_active = 1", (name,)
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM agents LIMIT 0").description]
        d = dict(zip(cols, row))
        # Parse JSON fields.
        for field in ("tool_allowlist", "handoff_to", "tags"):
            if d.get(field):
                d[field] = json.loads(d[field])
        return d

    def list_agents(self, active_only: bool = True) -> list[dict[str, Any]]:
        """List all agents."""
        import json
        q = "SELECT * FROM agents" + (" WHERE is_active = 1" if active_only else "")
        rows = self.conn.execute(q).fetchall()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM agents LIMIT 0").description]
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            for field in ("tool_allowlist", "handoff_to", "tags"):
                if d.get(field):
                    d[field] = json.loads(d[field])
            result.append(d)
        return result

    def deactivate_agent(self, name: str) -> bool:
        """Soft-delete an agent."""
        cur = self.conn.execute(
            "UPDATE agents SET is_active = 0, updated_at = datetime('now') WHERE name = ?",
            (name,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---------------------------------------------------------------- generations
    def record_generation(
        self,
        agent_name: str,
        task_description: str,
        status: str = "pending",
        manifest_json: str | None = None,
        module_path: str | None = None,
        error_message: str | None = None,
    ) -> int:
        """Record a generation attempt. Returns the generation ID."""
        cur = self.conn.execute(
            """INSERT INTO generations
               (agent_name, task_description, status, manifest_json, module_path, error_message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (agent_name, task_description, status, manifest_json, module_path, error_message),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore

    def complete_generation(
        self, gen_id: int, status: str, error_message: str | None = None
    ) -> None:
        """Mark a generation as completed."""
        self.conn.execute(
            """UPDATE generations
               SET status = ?, error_message = ?, completed_at = datetime('now')
               WHERE id = ?""",
            (status, error_message, gen_id),
        )
        self.conn.commit()

    def list_generations(self, agent_name: str | None = None, limit: int = 20) -> list[dict]:
        """List recent generations."""
        if agent_name:
            rows = self.conn.execute(
                "SELECT * FROM generations WHERE agent_name = ? ORDER BY id DESC LIMIT ?",
                (agent_name, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM generations ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        cols = [d[0] for d in self.conn.execute("SELECT * FROM generations LIMIT 0").description]
        return [dict(zip(cols, row)) for row in rows]

    # ---------------------------------------------------------------- versions
    def record_version(
        self, agent_name: str, manifest_json: str, module_content: str | None = None
    ) -> int:
        """Record a new version for an agent."""
        # Get the next version number.
        row = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM agent_versions WHERE agent_name = ?",
            (agent_name,),
        ).fetchone()
        next_version = (row[0] if row else 0) + 1
        cur = self.conn.execute(
            """INSERT INTO agent_versions (agent_name, version, manifest_json, module_content)
               VALUES (?, ?, ?, ?)""",
            (agent_name, next_version, manifest_json, module_content),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore

    def get_version(self, agent_name: str, version: int) -> dict | None:
        """Get a specific version."""
        row = self.conn.execute(
            "SELECT * FROM agent_versions WHERE agent_name = ? AND version = ?",
            (agent_name, version),
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM agent_versions LIMIT 0").description]
        return dict(zip(cols, row))
