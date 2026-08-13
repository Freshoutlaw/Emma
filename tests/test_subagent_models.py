"""Per-agent Ollama models for sub-agents (Agent Factory).

Coverage:
- LLMRouter.complete/stream bind to a specific Ollama model (model=...) and
  record last_served provider 'agent'; failures fall back to normal routing.
- Agent manifests parse an `ollama_model` (or `model`) field and instantiate
  agents with it set.
- AgentRouter dispatches named sub-agents ('coder: fix the bug') and runs the
  task with the remainder of the message.
- AgentFactory picks a model from the request (explicit or web search + LLM)
  and bakes it into the generated module + manifest.
"""

import asyncio
import types

from agents.agent_factory import AgentFactory
from agents.router import AgentRouter
from llm.router import LLMRouter
from orchestration.agent_registry import AgentManifest, AgentRegistry


# ---------------------------------------------------------------- llm binding
class _FakeLocal:
    def __init__(self, fail_models=()):
        self.calls = []
        self.fail_models = set(fail_models)

    def available_models(self):
        return ["default-local"]

    def is_available(self):
        return True

    async def complete(self, messages, temperature=0.7, max_tokens=4096, model=None):
        self.calls.append(model)
        if model in self.fail_models:
            raise RuntimeError("model unavailable")
        return f"answered by {model}"

    async def stream(self, messages, temperature=0.7, max_tokens=4096, model=None):
        self.calls.append(model)
        if model in self.fail_models:
            raise RuntimeError("model unavailable")
        yield f"tok-{model}"
        yield " done"


class _FakeCloud:
    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True

    async def complete(self, messages, temperature=0.7, max_tokens=4096):
        self.calls += 1
        return "cloud answer"

    async def stream(self, messages, temperature=0.7, max_tokens=4096):
        self.calls += 1
        yield "cloud"
        yield " answer"


def _router(local):
    r = LLMRouter(domain="localhost")
    r.local = local
    r.cloud = _FakeCloud()
    return r


def test_complete_binds_to_specific_model():
    local = _FakeLocal()
    router = _router(local)

    async def run():
        return await router.complete([{"role": "user", "content": "hi"}], model="qwen2.5-coder:7b")

    assert asyncio.run(run()) == "answered by qwen2.5-coder:7b"
    assert local.calls == ["qwen2.5-coder:7b"], "the bound model must be the one used"
    assert router.last_served == {"provider": "agent", "model": "qwen2.5-coder:7b"}
    assert router.cloud.calls == 0


def test_stream_binds_to_specific_model():
    local = _FakeLocal()
    router = _router(local)

    async def run():
        return [t async for t in router.stream([{"role": "user", "content": "hi"}], model="coder-m")]

    assert asyncio.run(run()) == ["tok-coder-m", " done"]
    assert local.calls == ["coder-m"]
    assert router.last_served == {"provider": "agent", "model": "coder-m"}


def test_bound_model_failure_falls_back_to_normal_routing():
    """A failing bound model must not fail the turn — the normal router path
    (default local model, then cloud) takes over."""
    local = _FakeLocal(fail_models={"gone:7b"})
    router = _router(local)

    async def run():
        return await router.complete([{"role": "user", "content": "hi"}], model="gone:7b")

    assert asyncio.run(run()) == "answered by default-local"
    assert local.calls == ["gone:7b", "default-local"], "bound model first, then normal routing"
    assert router.last_served == {"provider": "local", "model": "default-local"}


# ---------------------------------------------------------------- manifests
def test_manifest_parses_ollama_model(tmp_path):
    registry = AgentRegistry()
    f = tmp_path / "coder.yaml"
    f.write_text(
        "name: coder\ndescription: writes code\nclass: agents.gen_coder.Coder\n"
        "ollama_model: qwen2.5-coder:7b\ntool_allowlist:\n  - read_file\n",
        encoding="utf-8",
    )
    assert registry.load_manifests(tmp_path) == 1
    manifest = registry.get("coder")
    assert manifest is not None
    assert manifest.ollama_model == "qwen2.5-coder:7b"
    assert manifest.to_dict()["ollama_model"] == "qwen2.5-coder:7b"


def test_manifest_accepts_model_alias(tmp_path):
    registry = AgentRegistry()
    f = tmp_path / "coder.yaml"
    f.write_text(
        "name: coder\ndescription: writes code\nclass: agents.gen_coder.Coder\n"
        "model: deepseek-coder:6.7b\n",
        encoding="utf-8",
    )
    registry.load_manifests(tmp_path)
    assert registry.get("coder").ollama_model == "deepseek-coder:6.7b"


def test_manifest_defaults_to_none(tmp_path):
    registry = AgentRegistry()
    f = tmp_path / "plain.yaml"
    f.write_text(
        "name: plain\ndescription: a plain agent\nclass: agents.gen_plain.Plain\n",
        encoding="utf-8",
    )
    registry.load_manifests(tmp_path)
    assert registry.get("plain").ollama_model is None


def test_instantiate_sets_ollama_model(tmp_path, monkeypatch):
    (tmp_path / "stub_mod.py").write_text(
        "class StubAgent:\n    def __init__(self, pipeline):\n        self.pipeline = pipeline\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = AgentRegistry()
    registry._agents["coder"] = AgentManifest(
        name="coder",
        description="writes code",
        class_path="stub_mod.StubAgent",
        ollama_model="qwen2.5-coder:7b",
    )
    instance = registry.instantiate("coder", pipeline=object())
    assert instance is not None
    assert instance.ollama_model == "qwen2.5-coder:7b", "instantiated agents carry their model"


# ---------------------------------------------------------------- dispatch
class _FakeRegistry:
    def __init__(self, manifest, instance):
        self._manifest = manifest
        self._instance = instance

    def all_agents(self):
        return {self._manifest.name: self._manifest}

    def get(self, name):
        return self._manifest if name == self._manifest.name else None

    def instantiate(self, name, pipeline):
        return self._instance


class _StubSubAgent:
    def __init__(self):
        self.ran_with = None

    async def run(self, request):
        self.ran_with = request
        return types.SimpleNamespace(ok=True, output="coder worked", intent="coder", actions=[], memory_ids=[], error=None, pending_consent=None, handoff=None)


def _router_with_subagent(agent):
    manifest = AgentManifest(
        name="coder",
        description="writes code",
        class_path="agents.gen_coder.Coder",
        ollama_model="qwen2.5-coder:7b",
    )
    registry = _FakeRegistry(manifest, agent)
    stub_runner = types.SimpleNamespace(run=lambda m: None)
    pipeline = types.SimpleNamespace(
        agent_registry=registry,
        audit=types.SimpleNamespace(log=lambda *a, **k: None),
        llm=types.SimpleNamespace(route=lambda: "none"),
        rag=types.SimpleNamespace(augment=lambda q, k: asyncio.sleep(0) or ""),
        reasoning=types.SimpleNamespace(run=lambda m: None),
        control=types.SimpleNamespace(),
        episodic=types.SimpleNamespace(remember=lambda c, kind="episode", payload=None: asyncio.sleep(0) or "ep1"),
        # Built-in agents the dispatch loop references (never reached for 'coder').
        memory_agent=stub_runner,
        security_agent=stub_runner,
        self_improve=stub_runner,
        map_agent=stub_runner,
        supabase_query_agent=stub_runner,
        design_agent=stub_runner,
        research_agent=stub_runner,
        agent_factory=stub_runner,
        learning_agent=stub_runner,
    )
    return AgentRouter(pipeline), registry


def test_classify_routes_named_sub_agent():
    agent = _StubSubAgent()
    router, _ = _router_with_subagent(agent)

    async def run():
        return await router.classify("coder: fix the bug")

    result = asyncio.run(run())
    assert result["intent"] == "coder"
    assert result["task"] == "fix the bug"


def test_classify_routes_arrow_and_bare_name():
    agent = _StubSubAgent()
    router, _ = _router_with_subagent(agent)

    async def run():
        return await router.classify("coder -> refactor main.py")

    result = asyncio.run(run())
    assert result["intent"] == "coder"
    assert result["task"] == "refactor main.py"

    async def run2():
        return await router.classify("coder")

    result2 = asyncio.run(run2())
    assert result2["intent"] == "coder"
    assert result2["task"] == ""


def test_dispatch_runs_sub_agent_with_task():
    agent = _StubSubAgent()
    router, _ = _router_with_subagent(agent)

    async def run():
        return await router.dispatch("coder: refactor main.py")

    result = asyncio.run(run())
    assert result.ok is True
    assert result.output == "coder worked"
    assert agent.ran_with == "refactor main.py", "the task must be the remainder, not the full message"


# ---------------------------------------------------------------- factory
def test_factory_explicit_model():
    factory = object.__new__(AgentFactory)
    assert factory._explicit_model("make a coder agent that uses qwen2.5-coder:7b") == "qwen2.5-coder:7b"
    assert factory._explicit_model("use deepseek-coder:6.7b for it") == "deepseek-coder:6.7b"
    assert factory._explicit_model("model = qwen3:8b please") == "qwen3:8b"
    assert factory._explicit_model("just a plain agent") is None


async def _search_stub(*a, **k):
    return "qwen2.5-coder is a popular coding model"


async def _llm_pick_stub(messages, **k):
    return "qwen2.5-coder:7b"


async def _llm_wrong_stub(messages, **k):
    return "wrong:1b"


async def _search_unused(*a, **k):
    return "unused"


def test_factory_pick_model_via_search():
    factory = object.__new__(AgentFactory)
    pipeline = types.SimpleNamespace(
        control=types.SimpleNamespace(execute=_search_stub),
        llm=types.SimpleNamespace(complete=_llm_pick_stub),
    )
    factory.pipeline = pipeline

    out = asyncio.run(factory._pick_model("go online and search for the best coding agent"))
    assert out == "qwen2.5-coder:7b"


def test_factory_pick_model_explicit_beats_search():
    factory = object.__new__(AgentFactory)
    pipeline = types.SimpleNamespace(
        control=types.SimpleNamespace(execute=_search_unused),
        llm=types.SimpleNamespace(complete=_llm_wrong_stub),
    )
    factory.pipeline = pipeline
    out = asyncio.run(factory._pick_model("use qwen3:8b — no searching needed"))
    assert out == "qwen3:8b"


def test_factory_generate_module_and_manifest_carry_model():
    factory = object.__new__(AgentFactory)
    spec = factory._design_agent("Create a coder agent that searches the web")
    spec["ollama_model"] = "qwen2.5-coder:7b"

    module = factory._generate_module(spec)
    assert "ollama_model = 'qwen2.5-coder:7b'" in module
    assert "model=self.ollama_model or None" in module
    compile(module, "<generated>", "exec")  # the template must be valid Python

    manifest = factory._generate_manifest(spec)
    assert "ollama_model: qwen2.5-coder:7b" in manifest


def test_factory_default_model_is_none():
    factory = object.__new__(AgentFactory)
    spec = factory._design_agent("Create a coder agent that searches the web")
    module = factory._generate_module(spec)
    assert "ollama_model = None" in module
    compile(module, "<generated>", "exec")
    manifest = factory._generate_manifest(spec)
    assert "ollama_model: null" in manifest
