"""Orchestration Tier 6 — config-driven agent runtime + hot-reload.

New agents register at runtime from YAML manifest files in a
``data/agents/`` directory. A file watcher detects new/changed/deleted
manifests and updates the registry. The orchestrator routes over the
live registry, so adding a new agent is a file drop — no code change,
no restart.

Manifest format (YAML):

```yaml
name: my_agent
description: "Does something useful."
class: agents.my_module.MyAgent   # dotted import path
tool_allowlist:                    # optional, None = full catalog
  - read_file
  - list_dir
max_plan_steps: 5                  # optional bound
handoff_to:                        # optional: agents this one can hand off to
  - reasoning
  - control
tags:                              # optional: for routing policy
  - research
  - data
```

The registry loads manifests at startup and watches for changes.
Built-in agents (the ones in agents/router.py's Pipeline) are always
registered and cannot be overridden by manifests — manifests only add
new agents.
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentManifest:
    """Parsed agent manifest from a YAML file."""

    name: str
    description: str
    class_path: str  # dotted import path, e.g. "agents.my_module.MyAgent"
    tool_allowlist: Optional[list[str]] = None
    max_plan_steps: Optional[int] = None
    handoff_to: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # The Ollama model this agent runs its LLM calls on (sub-agent binding).
    # None = the normal router path (cloud gemma4 primary / local fallback).
    ollama_model: Optional[str] = None
    source_file: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "class_path": self.class_path,
            "tool_allowlist": self.tool_allowlist,
            "max_plan_steps": self.max_plan_steps,
            "handoff_to": self.handoff_to,
            "tags": self.tags,
            "ollama_model": self.ollama_model,
            "source_file": self.source_file,
        }


class AgentRegistry:
    """Live registry of agents — built-in + config-driven.

    Built-in agents are registered via ``register_builtin()`` at
    Pipeline init. Config-driven agents are loaded from YAML manifests
    via ``load_manifests()``.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentManifest] = {}
        self._instances: dict[str, Any] = {}  # name → agent instance
        self._lock = threading.Lock()
        self._watch_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._manifest_dir: Optional[Path] = None
        self._manifest_mtimes: dict[str, float] = {}

    # ---------------------------------------------------------------- built-in
    def register_builtin(self, name: str, description: str) -> None:
        """Register a built-in agent (cannot be overridden by manifests)."""
        with self._lock:
            self._agents[name] = AgentManifest(
                name=name,
                description=description,
                class_path="builtin",
            )

    # ---------------------------------------------------------------- manifests
    def load_manifests(self, manifest_dir: Path) -> int:
        """Load all YAML manifests from a directory. Returns count loaded."""
        if not manifest_dir.exists():
            manifest_dir.mkdir(parents=True, exist_ok=True)
            return 0

        count = 0
        for f in sorted(manifest_dir.glob("*.yaml")):
            try:
                manifest = self._parse_yaml(f)
                if manifest:
                    with self._lock:
                        if manifest.name in self._agents and self._agents[manifest.name].class_path == "builtin":
                            logger.warning("Manifest '%s' skipped — overrides a built-in agent.", manifest.name)
                            continue
                        self._agents[manifest.name] = manifest
                        self._manifest_mtimes[str(f)] = f.stat().st_mtime
                    count += 1
            except Exception as exc:
                logger.error("Failed to load manifest %s: %s", f, exc)

        # Also support .yml extension.
        for f in sorted(manifest_dir.glob("*.yml")):
            try:
                manifest = self._parse_yaml(f)
                if manifest:
                    with self._lock:
                        if manifest.name in self._agents and self._agents[manifest.name].class_path == "builtin":
                            continue
                        self._agents[manifest.name] = manifest
                        self._manifest_mtimes[str(f)] = f.stat().st_mtime
                    count += 1
            except Exception as exc:
                logger.error("Failed to load manifest %s: %s", f, exc)

        logger.info("Loaded %d agent manifests from %s", count, manifest_dir)
        return count

    def _parse_yaml(self, path: Path) -> Optional[AgentManifest]:
        """Parse a YAML manifest file. Returns None if invalid."""
        try:
            import yaml
        except ImportError:
            # Fallback: minimal YAML parser for simple manifests.
            return self._parse_yaml_fallback(path)

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict) or "name" not in data or "class" not in data:
            logger.warning("Invalid manifest at %s — missing required fields (name, class).", path)
            return None

        return AgentManifest(
            name=data["name"],
            description=data.get("description", ""),
            class_path=data["class"],
            tool_allowlist=data.get("tool_allowlist"),
            max_plan_steps=data.get("max_plan_steps"),
            handoff_to=data.get("handoff_to", []),
            tags=data.get("tags", []),
            ollama_model=data.get("ollama_model") or data.get("model"),
            source_file=str(path),
        )

    def _parse_yaml_fallback(self, path: Path) -> Optional[AgentManifest]:
        """Minimal YAML parser for simple manifests (no PyYAML dependency)."""
        data: dict[str, Any] = {}
        current_key = None
        current_list: list[str] = []

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                # Top-level key: value
                if ":" in stripped and not stripped.startswith("-"):
                    # Save previous list if any.
                    if current_key and current_list:
                        data[current_key] = current_list
                        current_list = []

                    key, _, value = stripped.partition(":")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")

                    if value:
                        data[key] = value
                        current_key = None
                    else:
                        current_key = key
                elif stripped.startswith("- ") and current_key:
                    current_list.append(stripped[2:].strip().strip('"').strip("'"))

        # Save final list.
        if current_key and current_list:
            data[current_key] = current_list

        if "name" not in data or "class" not in data:
            return None

        return AgentManifest(
            name=data["name"],
            description=data.get("description", ""),
            class_path=data["class"],
            tool_allowlist=data.get("tool_allowlist"),
            max_plan_steps=data.get("max_plan_steps"),
            handoff_to=data.get("handoff_to", []),
            tags=data.get("tags", []),
            ollama_model=data.get("ollama_model") or data.get("model"),
            source_file=str(path),
        )

    # ---------------------------------------------------------------- lookup
    def get(self, name: str) -> Optional[AgentManifest]:
        with self._lock:
            return self._agents.get(name)

    def all_agents(self) -> dict[str, AgentManifest]:
        with self._lock:
            return dict(self._agents)

    def by_tag(self, tag: str) -> list[AgentManifest]:
        with self._lock:
            return [a for a in self._agents.values() if tag in a.tags]

    def handoff_targets(self, source: str) -> list[str]:
        """Return agent names that `source` is allowed to hand off to."""
        manifest = self.get(source)
        if not manifest:
            return []
        return manifest.handoff_to

    # ---------------------------------------------------------------- instantiation
    def instantiate(self, name: str, pipeline: Any) -> Optional[Any]:
        """Instantiate an agent from its manifest. Caches the instance.

        For built-in agents (class_path="builtin"), returns None — the
        Pipeline owns those instances.
        """
        with self._lock:
            if name in self._instances:
                return self._instances[name]

        manifest = self.get(name)
        if not manifest or manifest.class_path == "builtin":
            return None

        try:
            module_path, class_name = manifest.class_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            instance = cls(pipeline)
            # Per-agent LLM binding: the generated agent reads this attribute
            # and passes it to pipeline.llm.complete/stream(model=...).
            instance.ollama_model = manifest.ollama_model
            with self._lock:
                self._instances[name] = instance
            logger.info("Instantiated agent '%s' from %s", name, manifest.class_path)
            return instance
        except Exception as exc:
            logger.error("Failed to instantiate agent '%s': %s", name, exc)
            return None

    # ---------------------------------------------------------------- watcher
    def start_watching(self, manifest_dir: Path, interval: float = 5.0) -> None:
        """Start a background thread that watches for manifest changes."""
        if self._watch_thread and self._watch_thread.is_alive():
            return

        self._manifest_dir = manifest_dir
        self._stop_event.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            args=(manifest_dir, interval),
            daemon=True,
            name="agent-registry-watcher",
        )
        self._watch_thread.start()
        logger.info("Started manifest watcher on %s (interval=%.1fs)", manifest_dir, interval)

    def stop_watching(self) -> None:
        """Stop the manifest watcher thread."""
        self._stop_event.set()
        if self._watch_thread:
            self._watch_thread.join(timeout=5.0)
            self._watch_thread = None

    def _watch_loop(self, manifest_dir: Path, interval: float) -> None:
        """Background loop that detects manifest changes."""
        while not self._stop_event.is_set():
            self._stop_event.wait(interval)
            if self._stop_event.is_set():
                break
            self._check_for_changes(manifest_dir)

    def _check_for_changes(self, manifest_dir: Path) -> None:
        """Scan the manifest directory for new/changed/deleted files."""
        current_files: dict[str, float] = {}

        for ext in ("*.yaml", "*.yml"):
            for f in manifest_dir.glob(ext):
                try:
                    current_files[str(f)] = f.stat().st_mtime
                except OSError:
                    continue

        with self._lock:
            old_files = dict(self._manifest_mtimes)

        # New or changed files.
        new_or_changed = []
        for fpath, mtime in current_files.items():
            if fpath not in old_files or old_files[fpath] != mtime:
                new_or_changed.append(Path(fpath))

        # Deleted files.
        deleted = [f for f in old_files if f not in current_files]

        # Process changes.
        for fpath in deleted:
            logger.info("Manifest removed: %s", fpath)
            with self._lock:
                # Find and remove the agent.
                to_remove = [name for name, m in self._agents.items() if m.source_file == fpath]
                for name in to_remove:
                    del self._agents[name]
                    self._instances.pop(name, None)
                    logger.info("Agent '%s' unloaded (manifest deleted).", name)
                self._manifest_mtimes.pop(fpath, None)

        for fpath in new_or_changed:
            try:
                manifest = self._parse_yaml(fpath)
                if manifest:
                    with self._lock:
                        if manifest.name in self._agents and self._agents[manifest.name].class_path == "builtin":
                            continue
                        is_update = manifest.name in self._agents
                        self._agents[manifest.name] = manifest
                        self._manifest_mtimes[str(fpath)] = fpath.stat().st_mtime
                        if is_update:
                            # Invalidate cached instance so it re-instantiates.
                            self._instances.pop(manifest.name, None)
                            logger.info("Agent '%s' updated from %s", manifest.name, fpath)
                        else:
                            logger.info("Agent '%s' loaded from %s", manifest.name, fpath)
            except Exception as exc:
                logger.error("Failed to reload manifest %s: %s", fpath, exc)

        # Update the mtime snapshot.
        with self._lock:
            self._manifest_mtimes = dict(current_files)
