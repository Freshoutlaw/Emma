"""Control agent — executes Emma's capabilities.

Holds the tool catalog that the reasoning agent plans against. Every tool call
goes through the Guardian (each capability gates itself), and the outcome is
audited.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

from agents.base import AgentResult, BaseAgent

if TYPE_CHECKING:
    from agents.router import Pipeline


class UnknownToolError(RuntimeError):
    pass


class ToolNotAllowedError(RuntimeError):
    """A tool exists in the catalog but the calling agent's allowlist forbids it.

    Raised at the execute boundary (never thrown past the router): the
    orchestrator layer scopes tools per agent, and this is the clean,
    data-shaped signal that an agent tried to run something outside its
    allowlist.
    """

    def __init__(self, tool: str, actor: str) -> None:
        super().__init__(f"tool '{tool}' is not available to agent '{actor}'")
        self.tool = tool
        self.actor = actor


class ControlAgent(BaseAgent):
    name = "control"
    description = "Executes system capabilities: files, shell, web, git, docker, mqtt, browser, desktop."

    # Tool catalog — used by the reasoning agent to plan tool calls.
    TOOL_CATALOG: dict[str, dict[str, Any]] = {
        "read_file": {"description": "Read a text file from disk.", "args": {"path": "str"}},
        "write_file": {"description": "Write content to a file (creates parent dirs).", "args": {"path": "str", "content": "str"}},
        "list_dir": {"description": "List a directory's contents.", "args": {"path": "str"}},
        "run_command": {"description": "Run a shell command and return its output.", "args": {"command": "str", "cwd": "str (optional)"}},
        "web_search": {"description": "Search the web and return result snippets.", "args": {"query": "str", "n": "int (default 5)"}},
        "fetch_page": {"description": "Fetch a URL and extract readable text.", "args": {"url": "str"}},
        "git_status": {"description": "Show git working tree status.", "args": {"cwd": "str (optional)"}},
        "git_log": {"description": "Show recent git commits.", "args": {"n": "int (default 10)", "cwd": "str (optional)"}},
        "git_commit": {"description": "Stage and commit changes.", "args": {"message": "str", "cwd": "str (optional)"}},
        "git_push": {"description": "Push commits to a remote.", "args": {"remote": "str (default origin)", "branch": "str (optional)", "cwd": "str (optional)"}},
        "docker_ps": {"description": "List docker containers.", "args": {}},
        "docker_images": {"description": "List docker images.", "args": {}},
        "docker_logs": {"description": "Show a container's logs.", "args": {"container": "str", "tail": "int (default 200)"}},
        "compose_up": {"description": "Bring up a docker compose stack in a directory.", "args": {"directory": "str"}},
        "compose_down": {"description": "Tear down a docker compose stack in a directory.", "args": {"directory": "str"}},
        "mqtt_publish": {"description": "Publish a message to the MQTT broker.", "args": {"topic": "str", "payload": "str"}},
        "browser_open": {"description": "Open a URL in a headless browser.", "args": {"url": "str"}},
        "browser_screenshot": {"description": "Screenshot the headless browser page.", "args": {"path": "str (optional)"}},
        "desktop_notify": {"description": "Show a desktop notification.", "args": {"title": "str", "message": "str"}},
        "desktop_screenshot": {"description": "Capture a screenshot of the desktop.", "args": {"path": "str (optional)"}},
    }

    def __init__(self, pipeline: "Pipeline") -> None:
        super().__init__(pipeline)
        self.io = pipeline.system_io
        self.web = pipeline.web_search
        self.git = pipeline.git_manager
        self.docker = pipeline.docker_manager
        self.mqtt = pipeline.mqtt
        self.browser = pipeline.browser
        self.desktop = pipeline.desktop

    def _tools(self) -> dict[str, Callable[..., Any]]:
        return {
            "read_file": self.io.read_file,
            "write_file": self.io.write_file,
            "list_dir": self.io.list_dir,
            "run_command": self.io.run_command,
            "web_search": self.web.search,
            "fetch_page": self.web.fetch_page_text,
            "git_status": self.git.status,
            "git_log": self.git.log,
            "git_commit": self.git.commit,
            "git_push": self.git.push,
            "docker_ps": self.docker.ps,
            "docker_images": self.docker.images,
            "docker_logs": self.docker.logs,
            "compose_up": self.docker.compose_up,
            "compose_down": self.docker.compose_down,
            "mqtt_publish": self.mqtt.publish,
            "browser_open": self.browser.open,
            "browser_screenshot": self.browser.screenshot,
            "desktop_notify": self.desktop.notify,
            "desktop_screenshot": self.desktop.screenshot,
        }

    # ---------------------------------------------------------------- scoping
    def _allowlist_for(self, actor: str) -> frozenset[str]:
        """The tool names `actor` is permitted to call.

        - 'control' (the executor itself / a direct user tool request) gets
          the full catalog — it IS the tool layer.
        - Any other agent gets its `tool_allowlist` (BaseAgent), or the full
          catalog if it never declared one.
        - The intersection with the live catalog keeps the allowlist honest
          if a tool is ever renamed or removed.
        """
        catalog = frozenset(self._tools())
        if actor == "control":
            return catalog
        agent = getattr(self.pipeline, actor, None)
        allow = getattr(agent, "tool_allowlist", None)
        if not allow:
            return catalog
        return catalog & frozenset(allow)

    # ---------------------------------------------------------------- execute
    async def execute(self, tool: str, actor: str = "control", **args: Any) -> str:
        """Run `tool` for `actor`, enforcing that actor's tool allowlist."""
        fn = self._tools().get(tool)
        if fn is None:
            raise UnknownToolError(f"unknown tool '{tool}'")
        allowed = self._allowlist_for(actor)
        if tool not in allowed:
            raise ToolNotAllowedError(tool, actor)
        result = await fn(**args)
        self.pipeline.audit.log(
            "control.executed", action=tool, actor=f"agent:{actor}", detail=args
        )
        if isinstance(result, str):
            return result
        return json.dumps(result, default=str)

    # ---------------------------------------------------------------- run
    async def run(self, request: str) -> AgentResult:
        """Run a single tool. `request` is JSON `{"tool": ..., "args": {...}}`
        or a bare tool name."""
        tool: str
        args: dict[str, Any] = {}
        try:
            spec = json.loads(request)
            if isinstance(spec, dict):
                tool = str(spec.get("tool", ""))
                args = spec.get("args", {}) or {}
            else:
                tool = request.strip()
        except json.JSONDecodeError:
            tool = request.strip()
        if not tool or tool not in self._tools():
            return AgentResult(
                ok=False,
                output=f"Unknown tool '{tool}'. Available: {', '.join(sorted(self._tools()))}",
                intent="control",
                error=f"unknown tool: {tool}",
            )
        try:
            output = await self.execute(tool, **args)
        except Exception as exc:  # surface tool errors to the router
            return AgentResult(ok=False, output=str(exc), intent="control", error=str(exc))
        return AgentResult(ok=True, output=output, intent="control", actions=[{"tool": tool, "args": args}])
