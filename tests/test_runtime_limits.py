"""Step-limit and tool-budget enforcement, using mocked models only."""

from __future__ import annotations

import pytest

from synvio.config import ExecutionModeNotImplemented, SYNVIOConfig
from synvio.runtime import (
    BudgetedTool,
    ToolBudget,
    ask,
    build_react_agent,
    engine_for,
    identify_engine,
    run_agent,
)

from support import RecordingTool, ScriptedEngine, react_action, react_final


def test_build_react_agent_reuses_openjarvis_loop():
    from openjarvis.agents.native_react import NativeReActAgent

    agent = build_react_agent(
        engine=ScriptedEngine([react_final()]),
        model="gpt-4o-mini",
        tools=[RecordingTool()],
        max_turns=2,
    )
    assert isinstance(agent, NativeReActAgent)
    assert agent._max_turns == 2


def test_agent_stops_at_step_limit():
    tool = RecordingTool()
    engine = ScriptedEngine([react_action("ping")] * 10)

    result = run_agent(
        task="loop forever",
        engine=engine,
        model="gpt-4o-mini",
        tools=[tool],
        max_turns=2,
    )

    assert result.turns == 2
    assert result.metadata.get("max_turns_exceeded") is True
    assert len(result.tool_results) == 2
    assert tool.calls == 2
    assert len(engine.calls) == 2


def test_tool_budget_denies_calls_beyond_limit():
    tool = RecordingTool()
    budget = ToolBudget(2)
    wrapped = BudgetedTool(tool, budget)

    result = run_agent(
        task="call ping many times",
        engine=ScriptedEngine([react_action("ping")] * 6),
        model="gpt-4o-mini",
        tools=[wrapped],
        max_turns=4,
    )

    assert budget.used == 2
    assert budget.denied == 2
    assert tool.calls == 2
    assert [r.success for r in result.tool_results] == [True, True, False, False]
    assert "budget exhausted" in result.tool_results[-1].content.lower()
    assert len(result.tool_results) == 4  # the loop kept going, the tool did not


def test_tool_budget_rejects_non_positive_limit():
    with pytest.raises(ValueError):
        ToolBudget(0)


def test_agent_receives_selected_model_and_reasoning_budget():
    engine = ScriptedEngine([react_final()])
    config = SYNVIOConfig(reasoning_mode="low")

    run_agent(
        task="hello",
        engine=engine,
        model=config.model,
        tools=[RecordingTool()],
        max_turns=2,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )

    call = engine.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert call["max_tokens"] == 512
    assert call["temperature"] == config.temperature


def test_ask_refuses_cloud_before_touching_an_engine():
    with pytest.raises(ExecutionModeNotImplemented):
        ask("hello", SYNVIOConfig(execution_mode="cloud"))


def test_ask_uses_jarvis_ask_full_with_mapped_config(monkeypatch):
    import openjarvis

    captured = {}

    class FakeJarvis:
        def __init__(self, *, config=None, engine_key=None, **kwargs):
            captured["config"] = config
            captured["engine_key"] = engine_key

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def ask_full(self, query, **kwargs):
            captured["query"] = query
            captured["kwargs"] = kwargs
            return {"content": "42", "tool_results": [], "turns": 0}

    monkeypatch.setattr(openjarvis, "Jarvis", FakeJarvis)

    config = SYNVIOConfig(reasoning_mode="low", agent_steps_limit=3)
    result = ask("what is 6*7?", config, tools=["calculator"])

    assert result["content"] == "42"
    assert captured["query"] == "what is 6*7?"
    assert captured["kwargs"]["model"] == "gpt-4o-mini"
    assert captured["kwargs"]["tools"] == ["calculator"]
    assert captured["kwargs"]["max_tokens"] == 512
    assert captured["config"].agent.max_turns == 3
    # The engine is pinned so a dead local engine cannot become a cloud call.
    assert captured["engine_key"] == "ollama"
    assert captured["config"].engine.default == "ollama"


def test_engine_for_pins_the_engine_and_closes_the_sdk(monkeypatch):
    import openjarvis

    captured = {}

    class FakeEngine:
        pass

    class FakeJarvis:
        def __init__(self, *, config=None, engine_key=None, **kwargs):
            captured["engine_key"] = engine_key
            self._engine = FakeEngine()

        def _ensure_engine(self):  # pragma: no cover - already "resolved"
            return None

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(openjarvis, "Jarvis", FakeJarvis)

    with engine_for(SYNVIOConfig(engine="ollama")) as engine:
        assert isinstance(engine, FakeEngine)

    assert captured == {"engine_key": "ollama", "closed": True}


def test_engine_for_reports_a_missing_engine_clearly(monkeypatch):
    import openjarvis

    class FakeJarvis:
        def __init__(self, *, config=None, engine_key=None, **kwargs):
            pass

        def _ensure_engine(self):
            raise RuntimeError("No inference engine available.")

        def close(self):
            # A failing close() must not hide the engine error.
            raise AssertionError("close() must not mask the engine error")

    monkeypatch.setattr(openjarvis, "Jarvis", FakeJarvis)

    with pytest.raises(RuntimeError) as excinfo:
        with engine_for(SYNVIOConfig(engine="ollama")):
            pass  # pragma: no cover - the context must fail first

    assert "ollama" in str(excinfo.value)


# -- engine identity -------------------------------------------------------
#
# The first paid run printed "InstrumentedEngine (cloud, is_cloud=False)" while
# the engine that answered was CloudEngine: OpenJarvis wraps the resolved engine
# for telemetry and the wrapper does not forward ``is_cloud``.


class CloudStub:
    """Minimal engine double that looks like the pinned cloud engine."""

    engine_id = "cloud"
    is_cloud = True
    _publishes_events = True

    def generate(self, messages, **kwargs):
        return {"content": "", "usage": {}}

    def list_models(self):
        return ["gpt-4o"]


class LocalStub(CloudStub):
    engine_id = "ollama"
    is_cloud = False


class NestedWrapper:
    """Stands in for GuardrailsEngine, which wraps through ``_engine``."""

    def __init__(self, engine):
        self._engine = engine

    def generate(self, messages, **kwargs):
        return self._engine.generate(messages, **kwargs)


def _instrumented(engine):
    from openjarvis.core.events import EventBus
    from openjarvis.telemetry.instrumented_engine import InstrumentedEngine

    return InstrumentedEngine(engine, EventBus())


def test_identify_engine_reads_the_real_engine_not_the_wrapper():
    wrapped = _instrumented(CloudStub())

    # The wrapper itself reports the opposite: this is exactly what the paid
    # run printed, and it is why the check must not read the wrapper.
    assert wrapped.is_cloud is False

    identity = identify_engine(wrapped)

    assert identity.chain == ["InstrumentedEngine", "CloudStub"]
    assert identity.is_wrapped is True
    assert identity.is_cloud is True
    assert identity.engine_id == "cloud"
    assert "InstrumentedEngine -> CloudStub" in identity.describe()
    assert identity.to_dict()["is_cloud"] is True


def test_identify_engine_does_not_invent_cloud_for_a_local_engine():
    identity = identify_engine(LocalStub())

    assert identity.chain == ["LocalStub"]
    assert identity.is_wrapped is False
    assert identity.is_cloud is False
    assert identity.engine_id == "ollama"


def test_identify_engine_unwraps_stacked_wrappers():
    identity = identify_engine(_instrumented(NestedWrapper(CloudStub())))

    assert identity.chain == ["InstrumentedEngine", "NestedWrapper", "CloudStub"]
    assert identity.is_cloud is True
    assert identity.engine_id == "cloud"


def test_identify_engine_survives_a_self_referential_wrapper():
    class Looping:
        engine_id = "loop"
        is_cloud = False
        _publishes_events = True

        def __init__(self):
            self._inner = self

        def generate(self, *args, **kwargs):
            return {"content": "", "usage": {}}

    identity = identify_engine(Looping())

    assert identity.chain == ["Looping"]
    assert identity.is_cloud is False
