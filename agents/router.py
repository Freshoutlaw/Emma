"""Agent router + Pipeline service container.

`Pipeline` is the composition root: it builds every service (guardian, LLM
router, memory, capabilities, agents) from settings. `AgentRouter` classifies
incoming requests and dispatches to the right agent, auto-remembering every
exchange as an episodic memory.

OPTIMIZATIONS:
- Cached intent classification
- Optimized keyword matching with compiled regex
- Efficient agent dispatch
- Resource pooling
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator, Optional
from functools import lru_cache

from backend.speak_pipeline import SpeakPipeline
from performance.turn_metrics import turn_metrics

from agents.base import AgentResult
from agents.control import (
    ControlAgent,
    ToolNotAllowedError,
    UnknownToolError,
    summarize_tool_output,
)
from agents.reasoning import SCREENSHOT_TOOLS
from orchestration.routing_policy import apply_policy, RoutingDecision
from orchestration.failure_isolation import failure_isolation
from orchestration.handoff import HandoffManager, HandoffProposal, create_handoff
from orchestration.agent_registry import AgentRegistry
from agents.map import MapAgent
from agents.memory import MemoryAgent
from agents.reasoning import ReasoningAgent
from agents.security import SecurityAgent
from agents.self_improve import SelfImproveAgent
from agents.supabase_query import SupabaseQueryAgent
from agents.design import DesignAgent
from agents.research import ResearchAgent
from agents.agent_factory import AgentFactory
from agents.learning import LearningAgent
from capabilities.browser_automation import BrowserAutomation
from capabilities.desktop_control import DesktopControl
from capabilities.docker_manager import DockerManager
from capabilities.git_manager import GitManager
from capabilities.learning_engine import LearningEngine
from capabilities.mqtt_home import MQTTClient
from capabilities.system_io import SystemIO
from capabilities.web_search import WebSearch
from cost.usage import UsageRepo, set_usage_repo
from flags.display import DisplayState
from flags.network_gate import NetworkGate
from interfaces.vision.mediapipe_handler import MediaPipeHandler
from interfaces.voice.stt import STTEngine
from interfaces.voice.tts import TTSEngine
from llm.local import LLMUnavailable
from llm.router import LLMRouter
from memory.embeddings import Embedder
from memory.episodic import EpisodicMemory
from memory.rag_pipeline import RAGPipeline
from memory.supabase_client import SupabaseClient
from security.audit_log import AuditLog
from security.consent_manager import ConsentManager
from security.guardian import ConsentRequiredError, Guardian
from security.kill_switch import KillSwitch

INTENTS = ("control", "memory", "security", "self_improve", "map", "reasoning", "supabase_query", "design", "research", "agent_factory", "learning", "chat")

INTENT_SYSTEM_PROMPT = (
    "You are Emma's intent router. Classify the user's request into exactly one intent: "
    "control, memory, security, self_improve, reasoning, supabase_query, learning, chat.\n"
    "- control: the user wants an action performed on the system (files, shell, web, git, docker, mqtt, browser, desktop)\n"
    "- memory: storing or recalling memories\n"
    "- security: kill switch, consent mode, network gate, or status reports\n"
    "- self_improve: changing Emma's own code or suggesting improvements to herself\n"
    "- map: questions about a location, region, map, geography, flights, or weather that should surface the region dashboard\n"
    "- supabase_query: querying a database (SQL, tables, schemas, listing tables, describing columns)\n"
    "- design: design tasks (mockups, scaffolds, tokens, component catalog, build preview)\n"
    "- research: web research, information gathering, summarizing findings\n"
    "- agent_factory: creating, building, or spawning new sub-agents\n"
    "- learning: comprehensive learning tasks where Emma should learn about a topic from its origins to present day\n"
    "- reasoning: complex multi-step tasks or questions that may need tools\n"
    "- chat: conversation that needs no tools\n"
    'Return ONLY JSON: {"intent": "<one intent>", "tool": "<tool name or null>", "args": {}}'
)

# Pre-compile regex patterns for performance
_PANEL_PATTERN = re.compile(r"^(?:show|open|display|hide|close)[ 	]+(?:the[ 	]+)?(memory|status|guardian|security|map|panels?)(?=[ 	]|$)", re.IGNORECASE)

# Pre-compile keyword sets for faster matching
_MEMORY_KEYWORDS = frozenset(["remember", "recall", "memory", "what did i"])
_SECURITY_KEYWORDS = frozenset(["kill switch", "security", "guardian", "consent", "network gate"])
_SUPABASE_KEYWORDS = frozenset(["query", "supabase", "database", "table", "sql"])
_DESIGN_KEYWORDS = frozenset(["design", "mockup", "scaffold", "tokens", "catalog", "component palette"])
_RESEARCH_KEYWORDS = frozenset(["research", "search for", "find information", "look up", "summarize", "what are the best"])
_AGENT_FACTORY_KEYWORDS = frozenset(["create agent", "build agent", "make agent", "spawn agent", "new agent", "agent factory"])
_LEARNING_KEYWORDS = frozenset(["learn", "teach me", "study", "go online and learn", "everything about", "comprehensive learning"])


# ----------------------------------------------------------------- vision live
# Sentinel returned by _parse_scene_json when the model clearly cannot see
# images (a text-only fallback served the call) — the live loop should stop
# instead of spamming the user with "I can't see" every frame.
_NO_VISION_MARKER = "__NO_VISION__"

_NO_VISION_HINTS = (
    "can't see",
    "cannot see",
    "not able to see",
    "no image",
    "don't have access",
    "can't access",
    "text-based model",
    "text only",
    "text-only",
    "unable to see",
)

_SCENE_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_scene_json(text: str) -> Optional[tuple[str, bool]]:
    """Parse the live-watch model reply ``{"scene": ..., "changed": ...}``.

    Returns ``(scene, changed)``, ``(None, False)`` for a transiently
    malformed reply, or ``(_NO_VISION_MARKER, False)`` when the reply shows
    the model cannot see images at all.
    """
    raw = text or ""
    lowered = raw.lower()
    if any(hint in lowered for hint in _NO_VISION_HINTS):
        return (_NO_VISION_MARKER, False)
    match = _SCENE_JSON_RE.search(raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    scene = str(data.get("scene", "")).strip()
    if not scene:
        return None
    changed = data.get("changed") in (True, "true", "True", "yes", 1, "1")
    return (scene, changed)
_SELF_IMPROVE_KEYWORDS = frozenset(["improve yourself", "self-improve", "review your code", "your own code", "rewrite yourself", "suggest improvements"])
_CONTROL_KEYWORDS = frozenset(["run ", "execute", "shell", "terminal", "file", "files", "folder", "directories", "create ", "delete ", "install ", "docker", "git ", "mqtt", "browser", "search the web", "web search", "open "])

MAP_WORDS = (
    "map", "maps", "where is", "where's", "where are", "where do", "location", "region",
    "geography", "geographic", "coordinates", "latitude", "longitude", "globe", "world view",
    "airport", "flight", "flights", "capital of", "weather in", "weather at", "time in",
    "time at", "distance between", "population of", "directions", "route to", "nearest",
)


def extract_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from free-form LLM text."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


MAP_WORDS = (
    "map", "maps", "where is", "where's", "where are", "where do", "location", "region",
    "geography", "geographic", "coordinates", "latitude", "longitude", "globe", "world view",
    "airport", "flight", "flights", "capital of", "weather in", "weather at", "time in",
    "time at", "distance between", "population of", "directions", "route to", "nearest",
)


MAP_WORDS = (
    "map", "maps", "where is", "where's", "where are", "where do", "location", "region",
    "geography", "geographic", "coordinates", "latitude", "longitude", "globe", "world view",
    "airport", "flight", "flights", "capital of", "weather in", "weather at", "time in",
    "time at", "distance between", "population of", "directions", "route to", "nearest",
)


@lru_cache(maxsize=1000)
def keyword_intent(message: str) -> str:
    """Optimized keyword intent classification with pre-compiled patterns."""
    low = message.lower().strip()
    
    # Use pre-compiled regex for panel commands
    panel = _PANEL_PATTERN.match(low)
    if panel:
        if panel.group(1) == "map":
            return "map"
        return "security" if panel.group(1) in ("status", "guardian", "security", "panel", "panels") else "memory"

    # "create a coder agent" / "assign it to a sub agent called coder" —
    # sub-agent creation requests route to the Agent Factory (sub-agents get
    # their own Ollama model).  Checked before the research/learning keywords
    # so "search for a X agent and assign it to a sub agent called Y" still
    # reaches the factory.
    if re.search(
        r"\b(?:create|build|make|spawn)\s+(?:a|an)\s+[\w\s-]+?\s+agent\b"
        r"|\b(?:sub\s*-?\s*agent|subagent)\b[^.]*\bcalled\s+[\w-]+"
        r"|\bassign\b[^.]*\bagent\b",
        low,
    ):
        return "agent_factory"

    # Continuous vision watch: "watch my screen" / "watch the browser" —
    # Emma keeps looking and speaks up only when something changes.
    # "stop watching" is checked first so it wins over "watch...".
    if "stop watching" in low or re.search(r"\b(?:stop|end)\s+(?:the\s+)?watch\b", low):
        return "watch_stop"
    m = re.search(r"\bwatch(?:ing)?\s+(?:my\s+|the\s+)?(screen|browser)\b", low)
    if m:
        return f"watch_{m.group(1)}"
    if "keep an eye on" in low:
        return "watch_browser" if "browser" in low else "watch_screen"

    # Use frozenset for O(1) keyword lookups
    if any(keyword in low for keyword in _MEMORY_KEYWORDS):
        return "memory"
    if any(word in low for word in MAP_WORDS):
        return "map"
    if any(keyword in low for keyword in _SECURITY_KEYWORDS):
        return "security"
    if any(keyword in low for keyword in _SUPABASE_KEYWORDS):
        return "supabase_query"
    if any(keyword in low for keyword in _DESIGN_KEYWORDS):
        return "design"
    if any(keyword in low for keyword in _RESEARCH_KEYWORDS):
        return "research"
    if any(keyword in low for keyword in _AGENT_FACTORY_KEYWORDS):
        return "agent_factory"
    if any(keyword in low for keyword in _LEARNING_KEYWORDS):
        return "learning"
    if (
        any(keyword in low for keyword in _SELF_IMPROVE_KEYWORDS)
        or low.startswith(("apply ", "verify ", "inspect "))
        or "apply patch" in low
    ):
        return "self_improve"
    if any(keyword in low for keyword in _CONTROL_KEYWORDS):
        return "control"
    return "reasoning"


class Pipeline:
    """Composition root — builds and owns every Emma service."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        data_dir = settings.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        settings.backups_dir.mkdir(parents=True, exist_ok=True)

        # --- security core
        self.audit = AuditLog(settings.audit_log_path, max_entries=settings.audit_max_entries)
        self.kill_switch = KillSwitch(settings.kill_switch_path)
        self.consent = ConsentManager(mode=settings.consent_mode, approval_ttl=settings.approval_ttl)
        self.guardian = Guardian(self.consent, self.audit, self.kill_switch)
        self.network_gate = NetworkGate(settings.network_gate_path, default_open=settings.network_gate_open)
        self.display = DisplayState()

        # --- cognition
        self.llm = LLMRouter(
            ollama_url=settings.ollama_url,
            groq_api_key=settings.groq_api_key,
            local_model=settings.local_model,
            cloud_model=settings.cloud_model,
            ollama_cloud_model=settings.ollama_cloud_model,
            domain=getattr(settings, 'domain', 'localhost'),
            num_ctx=getattr(settings, 'ollama_num_ctx', None),
            num_gpu=getattr(settings, 'ollama_num_gpu', None),
            keep_alive=getattr(settings, 'ollama_keep_alive', None),
        )
        self.embedder = Embedder(settings.ollama_url, settings.embedding_model, settings.embedding_dim)

        # --- memory
        self.supabase = SupabaseClient(
            settings.supabase_url,
            settings.supabase_anon_key,
            settings.supabase_service_key,
        )
        self.episodic = EpisodicMemory(settings.memory_db_path, self.embedder, self.supabase)
        self.rag = RAGPipeline(self.episodic, self.embedder, self.supabase)

        # --- capabilities
        self.system_io = SystemIO(self.guardian, settings)
        self.web_search = WebSearch(self.guardian, self.network_gate)
        self.browser = BrowserAutomation(self.guardian, self.network_gate)
        self.mqtt = MQTTClient(
            self.guardian,
            host=settings.mqtt_host,
            port=settings.mqtt_port,
            user=settings.mqtt_user,
            password=settings.mqtt_password,
            topic_prefix=settings.mqtt_topic_prefix,
        )
        self.git_manager = GitManager(self.system_io)
        self.docker_manager = DockerManager(self.system_io)
        self.desktop = DesktopControl(self.guardian)

        # --- interfaces
        self.stt = STTEngine(settings)
        self.tts = TTSEngine(settings)
        self.vision = MediaPipeHandler(settings)

        # --- cost dashboard
        self.usage_repo = UsageRepo(settings.usage_db_path)
        set_usage_repo(self.usage_repo)

        # --- agents
        self.control = ControlAgent(self)
        self.memory_agent = MemoryAgent(self)
        self.security_agent = SecurityAgent(self)
        self.self_improve = SelfImproveAgent(self)
        self.map_agent = MapAgent(self)
        self.supabase_query_agent = SupabaseQueryAgent(self)
        self.design_agent = DesignAgent(self)
        self.research_agent = ResearchAgent(self)
        self.agent_factory = AgentFactory(self)
        self.learning_agent = LearningAgent(self)
        self.reasoning = ReasoningAgent(self)

        # --- orchestration layer ---
        self.handoff_manager = HandoffManager()
        self.agent_registry = AgentRegistry()
        # Register built-in agents with the registry.
        for agent in (
            self.control, self.memory_agent, self.security_agent,
            self.self_improve, self.map_agent, self.supabase_query_agent,
            self.design_agent, self.research_agent, self.agent_factory,
            self.reasoning,
        ):
            self.agent_registry.register_builtin(agent.name, agent.description)
        # Load config-driven agent manifests and start the watcher.
        agents_dir = data_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        self.agent_registry.load_manifests(agents_dir)
        self.agent_registry.start_watching(agents_dir)

        self.router = AgentRouter(self)

    # ---------------------------------------------------------------- lifecycle
    async def close(self) -> None:
        self.agent_registry.stop_watching()
        await self.supabase.close()
        if hasattr(self, 'web_search') and hasattr(self.web_search, 'close'):
            await self.web_search.close()
        if hasattr(self.embedder, 'close'):
            await self.embedder.close()
        if hasattr(self.llm, 'local') and hasattr(self.llm.local, 'close'):
            await self.llm.local.close()
        if hasattr(self.llm, 'ollama_cloud') and self.llm.ollama_cloud is not None:
            await self.llm.ollama_cloud.close()
        if hasattr(self, 'episodic') and hasattr(self.episodic, 'close'):
            await self.episodic.close()
        await self.stt.close()
        await self.tts.close()
        self.mqtt.disconnect()
        await self.browser.close()
        if self.supabase_query_agent._pool is not None:
            await self.supabase_query_agent._pool.close()


class AgentRouter:
    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline

    # ---------------------------------------------------------------- sub-agents
    def _named_agent(self, message: str) -> Optional[tuple[str, str]]:
        """Explicit sub-agent routing: 'coder: fix the bug' / 'coder -> fix'.

        Config-driven agents (created by the Agent Factory) are dispatched by
        name — the first word of the message must be the agent's name followed
        by ':', ',' or '->'.  Returns (agent_name, task) or None.  The task is
        the remainder so the sub-agent doesn't have to re-parse its own name.
        """
        low = (message or "").strip().lower()
        if not low:
            return None
        registry = getattr(self.pipeline, "agent_registry", None)
        if registry is None:
            return None
        for name, manifest in registry.all_agents().items():
            if manifest.class_path == "builtin" or len(name) < 3:
                continue
            prefix = name.lower()
            if not low.startswith(prefix):
                continue
            rest = low[len(prefix):].lstrip()
            if not rest:
                return name, ""
            if rest[0] in (":", ",") or rest.startswith("->"):
                task = message.strip()[len(prefix):].lstrip(" :,") 
                if task.startswith("->"):
                    task = task[2:].strip()
                return name, task.strip()
        return None

    # ---------------------------------------------------------------- classify
    async def classify(self, message: str) -> dict:
        """Return {"intent": ..., "tool": ..., "args": {...}}.

        Decisive keyword matches (remember, kill switch, docker, …) take a
        fast path so a slow or absent LLM never blocks simple requests. The
        LLM is consulted only for ambiguous intents, with a hard timeout.
        """
        # Sub-agent names beat everything: 'coder: fix the bug' routes to the
        # 'coder' sub-agent with task 'fix the bug'.
        named = self._named_agent(message)
        if named:
            name, task = named
            return {"intent": name, "tool": None, "args": {}, "task": task}
        keyword = keyword_intent(message)

        # Fast path: short conversational messages (greetings, small talk)
        # skip the LLM intent call entirely — the reasoning plan step handles
        # any tool detection, and this saves a full LLM round trip per turn
        # while avoiding the clarify-on-chat prompt for e.g. "hello world".
        if keyword == "reasoning" and len(message.split()) <= 5:
            turn_metrics.record_classify(fast_path=True)
            return {"intent": "reasoning", "tool": None, "args": {}}

        # Tier 1 routing policy: try LLM for ambiguous (keyword == reasoning),
        # then apply policy (decomposition, tiebreak, clarify).
        llm_result = None
        if keyword == "reasoning" and self.pipeline.llm.route() != "none":
            turn_metrics.record_classify(fast_path=False)
            try:
                text = await asyncio.wait_for(
                    self.pipeline.llm.complete(
                        [
                            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                            {"role": "user", "content": message},
                        ],
                        temperature=0.0,
                        max_tokens=200,
                    ),
                    timeout=15,
                )
                parsed = extract_json(text)
                if parsed and parsed.get("intent") in INTENTS:
                    llm_result = parsed
            except Exception:
                pass

        # Apply routing policy (decomposition, tiebreak, clarify).
        policy = apply_policy(message, keyword, llm_result)

        # If policy says clarify, return the clarification as a result.
        if policy.clarify:
            return {"intent": "clarify", "tool": None, "args": {}, "clarify": policy.clarify}

        # If decomposed, return the first part (stream handles one at a time).
        if policy.decomposed:
            return {"intent": policy.intents[0], "tool": None, "args": {}, "decomposed": policy.messages}

        final_intent = policy.intents[0] if policy.intents else keyword
        return {"intent": final_intent, "tool": None, "args": {}}

    # ---------------------------------------------------------------- dispatch
    async def _dispatch_sub_agent(self, intent: str, task: str) -> Optional[AgentResult]:
        """Run a config-driven sub-agent (Agent Factory) by name, if registered.

        Returns None when the intent is not a manifest agent or the agent
        can't be instantiated — the caller falls through to reasoning.
        """
        registry = getattr(self.pipeline, "agent_registry", None)
        if registry is None:
            return None
        manifest = registry.get(intent)
        if manifest is None or manifest.class_path == "builtin":
            return None
        agent = registry.instantiate(intent, self.pipeline)
        if agent is None:
            return None
        try:
            return await asyncio.wait_for(agent.run(task), timeout=300)
        except ConsentRequiredError as exc:
            return AgentResult(
                ok=False,
                output="",
                intent=intent,
                error="consent required",
                pending_consent=exc.decision.to_dict(),
            )
        except Exception as exc:
            return AgentResult(
                ok=False,
                output=str(exc),
                intent=intent,
                error=str(exc),
            )

    # ---------------------------------------------------------------- dispatch
    async def dispatch(self, message: str) -> AgentResult:
        classification = await self.classify(message)
        intent = classification.get("intent", "reasoning")
        tool = classification.get("tool")
        args = classification.get("args") or {}
        task = classification.get("task") or message

        # Tier 1: handle clarify response.
        if intent == "clarify":
            return AgentResult(ok=True, output=classification.get("clarify", ""), intent="clarify")

        # Continuous vision watch intents are HUD-stream driven (the watch
        # SSE stream lives on an open client connection); the non-stream path
        # acknowledges so it doesn't fall through to a one-shot screenshot.
        if intent in ("watch_screen", "watch_browser", "watch_stop"):
            source = "screen" if intent == "watch_screen" else ("browser" if intent == "watch_browser" else "watch")
            return AgentResult(
                ok=True,
                output=(
                    f"👁 I'll watch the {source} and speak up when something changes. "
                    "Keep the HUD open so I can keep reporting."
                    if source != "watch"
                    else "👁 Watch stopped."
                ),
                intent=intent,
                actions=[],
            )

        # Tier 3: failure isolation — check circuit before dispatch.
        allowed, reason = failure_isolation.can_call(intent)
        if not allowed:
            failure_isolation.reject(intent)
            self.pipeline.audit.log("dispatch.circuit_open", action=intent, detail={"reason": reason})
            return AgentResult(ok=False, output=reason, intent=intent, error="circuit_open")

        if intent == "control":
            if tool:
                try:
                    output = await self.pipeline.control.execute(tool, **args)
                    if isinstance(output, bytes) and tool in SCREENSHOT_TOOLS:
                        # Screenshot tools return raw image bytes — narrate what
                        # the vision model sees instead of handing back a blob.
                        text = await self.pipeline.reasoning._synthesize(
                            message,
                            "",
                            [summarize_tool_output(tool, output)],
                            images=[output],
                        )
                        return AgentResult(ok=True, output=text, intent="control", actions=[{"tool": tool, "args": args}])
                    return AgentResult(ok=True, output=output, intent="control", actions=[{"tool": tool, "args": args}])
                except ConsentRequiredError as exc:
                    return AgentResult(ok=False, output="", intent="control", error="consent required", pending_consent=exc.decision.to_dict())
                except UnknownToolError:
                    pass  # LLM named a tool we don't have — let reasoning plan it
            # No usable tool was named: fall back to the reasoning agent,
            # which plans tool calls from natural language.
            return await self.pipeline.reasoning.run(message)

        # Agents other than control can also hit the guardian (e.g. a HIGH
        # `self_modify`). Turn ConsentRequiredError into a pending-consent
        # result so the HTTP layer returns 409 with a token instead of 500.
        for intent_name, runner in (
            ("memory", self.pipeline.memory_agent.run),
            ("security", self.pipeline.security_agent.run),
            ("self_improve", self.pipeline.self_improve.run),
            ("map", self.pipeline.map_agent.run),
            ("supabase_query", self.pipeline.supabase_query_agent.run),
            ("design", self.pipeline.design_agent.run),
            ("research", self.pipeline.research_agent.run),
            ("agent_factory", self.pipeline.agent_factory.run),
            ("learning", self.pipeline.learning_agent.run),
        ):
            if intent == intent_name:
                try:
                    return await runner(message)
                except ConsentRequiredError as exc:
                    return AgentResult(
                        ok=False,
                        output="",
                        intent=intent_name,
                        error="consent required",
                        pending_consent=exc.decision.to_dict(),
                    )
        # Config-driven sub-agents (Agent Factory) — each runs its own Ollama
        # model.  Falls through to reasoning when the intent isn't one.
        sub_result = await self._dispatch_sub_agent(intent, task)
        if sub_result is not None:
            return sub_result
        return await self.pipeline.reasoning.run(message)

        # Tier 3: record outcome for failure isolation.
        # (Success is recorded after the result is returned; failure is
        # recorded in the exception handler in run/stream.)

    # ---------------------------------------------------------------- run
    async def run(self, message: str, session_id: Optional[str] = None) -> AgentResult:
        self.pipeline.audit.log("chat.incoming", action="chat", actor="user", detail={"message": message[:500], "session": session_id})
        start = time.perf_counter()
        result = await self.dispatch(message)
        turn_metrics.record_turn(result.intent, time.perf_counter() - start)
        # Tier 3: record success/failure for circuit breaker.
        if result.ok:
            failure_isolation.record_success(result.intent)
        else:
            failure_isolation.record_failure(result.intent)
        try:
            episode_id = await self.pipeline.episodic.remember(message, kind="user", payload={"session": session_id, "intent": result.intent})
            if episode_id not in result.memory_ids:
                result.memory_ids.append(episode_id)
        except Exception:
            pass
        self.pipeline.audit.log(
            "chat.completed",
            action=result.intent,
            actor="router",
            decision={"ok": result.ok},
            detail={"output": result.output[:500]},
        )
        return result

    # ---------------------------------------------------------------- stream
    async def _stream_inner(self, message: str) -> AsyncIterator[dict]:
        """Yield SSE-style event dicts: token / action / consent / memory / done."""
        classification = await self.classify(message)
        intent = classification.get("intent", "reasoning")
        # Fresh per turn: the HUD shows which model actually served the reply
        # ("[via gemma4:31b-cloud]" vs "[via local qwen3.5:2b fallback]").
        self.pipeline.llm.last_served = None

        # Continuous vision watch — the turn asks the HUD to open the watch
        # SSE stream (the actual watching lives there); the display event
        # carries the source so the client knows what to capture.
        if intent in ("watch_screen", "watch_browser"):
            source = "screen" if intent == "watch_screen" else "browser"
            yield {"type": "display", "display": {"watch": source}}
            result = AgentResult(
                ok=True,
                output=(
                    f"👁 I'll watch the {source} and speak up when something changes. "
                    "Say 'stop watching' or tap the active watch button to end it."
                ),
                intent=intent,
                actions=[],
            )
            yield {"type": "done", "result": result.to_dict()}
            return
        if intent == "watch_stop":
            yield {"type": "display", "display": {"watch_stop": True}}
            result = AgentResult(ok=True, output="👁 Watch stopped.", intent="watch_stop", actions=[])
            yield {"type": "done", "result": result.to_dict()}
            return

        if intent in ("chat", "reasoning"):
            context = await self.pipeline.rag.augment(message, k=4)
            steps: list[dict] = []
            if intent == "reasoning":
                steps = await self.pipeline.reasoning.plan(message, context)

            actions: list[dict] = []
            outputs: list[str] = []
            images: list[bytes] = []
            for step in steps:
                tool = step.get("tool")
                args = step.get("args", {}) or {}
                if not tool:
                    continue
                try:
                    output = await self.pipeline.control.execute(tool, actor="reasoning", **args)
                except ConsentRequiredError as exc:
                    yield {"type": "consent", "decision": exc.decision.to_dict()}
                    return
                except ToolNotAllowedError:
                    outputs.append(
                        f"[step '{tool}' skipped — not in the reasoning agent's tool allowlist]"
                    )
                    continue
                except Exception as exc:
                    outputs.append(f"[step '{tool}' failed: {exc}]")
                    continue
                actions.append({"tool": tool, "args": args})
                if isinstance(output, bytes) and tool in SCREENSHOT_TOOLS:
                    # Screenshot bytes feed the vision model; the terminal
                    # sees a short placeholder instead of byte garbage.
                    images.append(output)
                    note = summarize_tool_output(tool, output)
                    outputs.append(note)
                    yield {"type": "action", "action": {"tool": tool, "output": note}}
                else:
                    outputs.append(output)
                    yield {"type": "action", "action": {"tool": tool, "output": output[:300]}}

            yield {"type": "actions", "actions": actions}

            text = ""
            stream_ok = True
            stream_error: Optional[str] = None
            # --- pipelined speak path: hold-one-ahead sentence segmentation ---
            speak = SpeakPipeline()
            try:
                async for token in self.pipeline.reasoning.stream_narration(message, context, outputs, images or None):
                    text += token
                    yield {"type": "token", "text": token}
                    for event in speak.feed(token):
                        yield event

                for event in speak.finish():
                    yield event

            except LLMUnavailable:
                # The provider vanished mid-stream.  A fallback notice is still
                # delivered, but the LLM narration failed — the turn must be
                # recorded as a failure, not a success.
                stream_ok = False
                stream_error = "llm_unavailable"
                fallback = "\n".join(outputs) if outputs else (
                    "⚠ No LLM provider available (start Ollama or set GROQ_API_KEY)."
                )
                text = fallback
                yield {"type": "token", "text": fallback}
            except Exception as exc:
                stream_ok = False
                stream_error = str(exc)
                text = f"⚠ error: {exc}"
                yield {"type": "token", "text": text}

            result = AgentResult(ok=stream_ok, output=text, intent=intent, actions=actions, error=stream_error)
            try:
                episode_id = await self.pipeline.episodic.remember(message, kind="user", payload={"intent": intent})
                result.memory_ids.append(episode_id)
                yield {"type": "memory", "episode": {"id": episode_id, "content": message, "kind": "user"}}
            except Exception:
                pass
            # Surface streamed outcomes in the audit log with the same shape as
            # run()'s chat.completed: a mid-stream LLM failure carries
            # decision.ok=false (with the error), so it is observable in
            # /api/system/activity instead of the turn looking like a success.
            self.pipeline.audit.log(
                "chat.completed",
                action=intent,
                actor="router",
                decision={"ok": stream_ok},
                detail={"output": text[:500], "error": stream_error},
            )
            yield {"type": "done", "result": result.to_dict(), "served_by": self.pipeline.llm.last_served}
            return

        if intent == "map":
            result = await self.pipeline.map_agent.run(message)
            if result.pending_consent:
                yield {"type": "consent", "decision": result.pending_consent}
                return
            # Push the display change immediately (polling also covers it).
            yield {"type": "display", "display": self.pipeline.display.state()}
            try:
                episode_id = await self.pipeline.episodic.remember(message, kind="user", payload={"intent": "map"})
                if episode_id not in result.memory_ids:
                    result.memory_ids.append(episode_id)
                yield {"type": "memory", "episode": {"id": episode_id, "content": message, "kind": "user"}}
            except Exception:
                pass
            yield {"type": "done", "result": result.to_dict()}
            return

        result = await self.dispatch(message)
        if result.pending_consent:
            yield {"type": "consent", "decision": result.pending_consent}
            return

        # Tier 5: if the result includes a handoff proposal, emit it.
        if hasattr(result, "handoff") and result.handoff:
            proposal = self.pipeline.handoff_manager.propose(result.handoff)
            self.pipeline.audit.log(
                "handoff.proposed",
                action=proposal.target_agent,
                actor=f"agent:{proposal.source_agent}",
                detail={"description": proposal.description},
            )
            yield {"type": "handoff", "proposal": proposal.to_dict()}

        yield {"type": "done", "result": result.to_dict(), "served_by": self.pipeline.llm.last_served}

    # ---------------------------------------------------------------- vision
    async def stream_vision(self, message: str, image_bytes: bytes) -> AsyncIterator[dict]:
        """SSE events for the HUD's 'describe this image' flow.

        The user shares an image through the HUD; Emma narrates what she sees
        with the vision-capable model (gemma4 cloud primary, local/Groq
        fallbacks via the LLM router).  No tools are planned — a pure vision
        turn: the image rides the narration message as Ollama `images`
        content (LocalLLM base64-encodes it) and the reply streams + speaks
        exactly like a normal chat turn.
        """
        self.pipeline.llm.last_served = None
        prompt = message.strip() or "Describe this image."
        context = await self.pipeline.rag.augment(prompt, k=4)

        text = ""
        stream_ok = True
        stream_error: Optional[str] = None
        # --- pipelined speak path: hold-one-ahead sentence segmentation ---
        speak = SpeakPipeline()
        try:
            async for token in self.pipeline.reasoning.stream_narration(
                prompt, context, [], images=[image_bytes]
            ):
                text += token
                yield {"type": "token", "text": token}
                for event in speak.feed(token):
                    yield event

            for event in speak.finish():
                yield event

        except LLMUnavailable:
            stream_ok = False
            stream_error = "llm_unavailable"
            fallback = "⚠ No LLM provider available (start Ollama or set GROQ_API_KEY) to describe the image."
            text = fallback
            yield {"type": "token", "text": fallback}
        except Exception as exc:
            stream_ok = False
            stream_error = str(exc)
            text = f"⚠ error: {exc}"
            yield {"type": "token", "text": text}

        result = AgentResult(
            ok=stream_ok, output=text, intent="vision", actions=[], error=stream_error
        )
        try:
            episode_id = await self.pipeline.episodic.remember(
                prompt, kind="user", payload={"intent": "vision", "has_image": True}
            )
            result.memory_ids.append(episode_id)
            yield {
                "type": "memory",
                "episode": {"id": episode_id, "content": prompt, "kind": "user"},
            }
        except Exception:
            pass
        self.pipeline.audit.log(
            "chat.completed",
            action="vision",
            actor="router",
            decision={"ok": stream_ok},
            detail={"output": text[:500], "error": stream_error},
        )
        yield {"type": "done", "result": result.to_dict(), "served_by": self.pipeline.llm.last_served}

    # ---------------------------------------------------------- vision live
    _LIVE_SCENE_SYSTEM = (
        "You are watching a live image feed. The user will show you the current "
        "frame and optionally the previous scene description. Reply ONLY with "
        "JSON: {\"scene\": \"<one short sentence describing what is in the current "
        "frame>\", \"changed\": true|false}. Set changed=true only when the content "
        "of the scene meaningfully changed (objects appeared, disappeared, moved, "
        "or a large color/layout shift) \u2014 never for mere wording differences of "
        "the same scene. If there is no previous scene, set \"changed\" to false."
    )

    async def _fetch_image_url(self, url: str) -> bytes:
        """Fetch a live image URL (webcam/monitoring feed) and validate it is a
        PNG/JPEG so a non-image page fails fast instead of feeding the model
        garbage."""
        import httpx

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.content
        if not (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff")):
            raise ValueError("URL did not return a PNG/JPEG image")
        return data

    async def stream_vision_live(
        self,
        image: bytes | str = b"",
        message: str = "",
        interval_seconds: float = 5.0,
        max_seconds: int = 600,
        source: str = "image",
        min_change_interval: float = 15.0,
        _fetcher=None,
    ) -> AsyncIterator[dict]:
        """SSE events for the HUD's live-watch mode.

        Emma keeps analyzing a changing image source every ``interval_seconds``
        and speaks up only when the scene actually changes.  One LLM call per
        frame produces a short scene line AND a model-judged ``changed`` flag,
        so wording drift from the vision model never triggers a false alarm.

        Sources:
        - ``image``: a static frame (``image`` bytes) or a live URL string
          (webcam/monitoring feed) re-fetched every frame.
        - ``screen``: a fresh desktop screenshot every frame (consent-gated).
        - ``browser``: a fresh headless-browser screenshot every frame.

        ``min_change_interval`` throttles change reports ("only when needed")
        so a busy screen doesn't make Emma chatter: after reporting a change
        she stays quiet for that many seconds (the scene baseline still
        tracks, so a distinct new change afterwards is reported).

        Events: vision_start / vision_heartbeat / vision_change (with a
        speak_segment so the update is spoken) / consent / vision_error /
        vision_stop.  ``_fetcher`` is injectable for tests (defaults to HTTP
        fetch).
        """
        self.pipeline.llm.last_served = None
        prompt = (message or "").strip()
        start = time.monotonic()
        frame = 0
        last_scene: Optional[str] = None
        errors = 0
        last_change_ts: Optional[float] = None
        consent_yielded = False
        stop_reason = "finished"
        emitted_terminal = False  # a vision_error already ended the stream
        # One SpeakPipeline per session: a stable base_turn_id so the HUD's
        # audio queue accepts every change segment (it drops segments from
        # other turns).
        speak = SpeakPipeline()

        async def load_frame() -> bytes:
            if source == "screen":
                return await self.pipeline.desktop.screenshot()
            if source == "browser":
                return await self.pipeline.browser.screenshot()
            if isinstance(image, bytes):
                return image
            fn = _fetcher or self._fetch_image_url
            return await fn(image)

        async def describe(frame_bytes: bytes, previous: Optional[str]):
            messages = [
                {"role": "system", "content": self._LIVE_SCENE_SYSTEM},
                {
                    "role": "user",
                    "content": f"Previous scene: {previous or 'none'}",
                    "images": [frame_bytes],
                },
            ]
            try:
                text = await asyncio.wait_for(
                    self.pipeline.llm.complete(messages, temperature=0.3, max_tokens=140),
                    timeout=30,
                )
            except Exception:
                return None
            return _parse_scene_json(text)

        try:
            while True:
                if time.monotonic() - start >= max_seconds:
                    stop_reason = "time limit reached"
                    break
                try:
                    frame_bytes = await load_frame()
                except ConsentRequiredError as exc:
                    # Screen/browser capture is consent-gated (Guardian): ask
                    # the operator through the HUD banner and keep retrying
                    # until approved — the banner's approval resolves the
                    # pending token server-side and the next capture succeeds.
                    if not consent_yielded:
                        yield {"type": "consent", "decision": exc.decision.to_dict()}
                        consent_yielded = True
                    await asyncio.sleep(interval_seconds)
                    continue
                except Exception as exc:
                    yield {
                        "type": "vision_error",
                        "message": f"could not load the image: {exc}",
                        "retry": True,  # the feed may come back — client reconnects
                    }
                    emitted_terminal = True
                    stop_reason = "source error"
                    break
                frame += 1
                result = await describe(frame_bytes, last_scene)
                if result is None:
                    errors += 1
                    if errors >= 3:
                        yield {
                            "type": "vision_error",
                            "message": "Emma's vision model is not responding — check Ollama and try again.",
                            "retry": True,
                        }
                        emitted_terminal = True
                        stop_reason = "model error"
                        break
                    continue  # transient — keep watching
                scene, changed = result
                if scene == _NO_VISION_MARKER:
                    yield {
                        "type": "vision_error",
                        "message": "Emma's current model can't see images — live mode needs a vision-capable model (gemma4 cloud, or a local vision model).",
                        "retry": False,  # a text-only model will never see — don't loop
                    }
                    emitted_terminal = True
                    stop_reason = "no vision"
                    break
                if last_scene is None:
                    yield {"type": "vision_start", "description": scene}
                elif changed and (
                    last_change_ts is None
                    or time.monotonic() - last_change_ts >= min_change_interval
                ):
                    last_change_ts = time.monotonic()
                    yield {"type": "vision_change", "description": scene, "frame": frame}
                    # Speak the update aloud through the pipelined TTS path.
                    speak.feed(scene)
                    for event in speak.finish():
                        yield event
                else:
                    yield {"type": "vision_heartbeat", "frame": frame}
                last_scene = scene
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            stop_reason = "disconnected"
            raise
        finally:
            try:
                self.pipeline.audit.log(
                    "vision.live.ended",
                    action="vision_live",
                    actor="router",
                    detail={"frames": frame, "reason": stop_reason, "prompt": prompt[:200]},
                )
            except Exception:
                pass
        if not emitted_terminal:
            yield {"type": "vision_stop", "reason": stop_reason, "frames": frame}

    # ---------------------------------------------------------------- stream
    async def stream(self, message: str) -> AsyncIterator[dict]:
        """Yield SSE-style event dicts: token / action / consent / memory / done.

        Thin wrapper over _stream_inner: times the whole turn so per-turn
        latency and intent land in /api/performance no matter which branch
        finishes it (done, consent, or an abandoned stream).
        """
        start = time.perf_counter()
        intent = "incomplete"
        try:
            async for event in self._stream_inner(message):
                if event.get("type") == "done":
                    result = event.get("result") or {}
                    intent = result.get("intent") or intent
                yield event
        finally:
            turn_metrics.record_turn(intent, time.perf_counter() - start)
