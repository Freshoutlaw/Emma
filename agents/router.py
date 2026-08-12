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
from agents.control import ControlAgent, ToolNotAllowedError, UnknownToolError
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

    # ---------------------------------------------------------------- classify
    async def classify(self, message: str) -> dict:
        """Return {"intent": ..., "tool": ..., "args": {...}}.

        Decisive keyword matches (remember, kill switch, docker, …) take a
        fast path so a slow or absent LLM never blocks simple requests. The
        LLM is consulted only for ambiguous intents, with a hard timeout.
        """
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
    async def dispatch(self, message: str) -> AgentResult:
        classification = await self.classify(message)
        intent = classification.get("intent", "reasoning")
        tool = classification.get("tool")
        args = classification.get("args") or {}

        # Tier 1: handle clarify response.
        if intent == "clarify":
            return AgentResult(ok=True, output=classification.get("clarify", ""), intent="clarify")

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

        if intent in ("chat", "reasoning"):
            context = await self.pipeline.rag.augment(message, k=4)
            steps: list[dict] = []
            if intent == "reasoning":
                steps = await self.pipeline.reasoning.plan(message, context)

            actions: list[dict] = []
            outputs: list[str] = []
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
                outputs.append(output)
                yield {"type": "action", "action": {"tool": tool, "output": output[:300]}}

            yield {"type": "actions", "actions": actions}

            text = ""
            stream_ok = True
            stream_error: Optional[str] = None
            # --- pipelined speak path: hold-one-ahead sentence segmentation ---
            speak = SpeakPipeline()
            try:
                async for token in self.pipeline.reasoning.stream_narration(message, context, outputs):
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

        yield {"type": "done", "result": result.to_dict()}

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
