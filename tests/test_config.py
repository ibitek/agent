"""Tests for SYNVIO configuration, model selection and execution modes."""

from __future__ import annotations

import pytest

from synvio.config import (
    DEFAULT_MAX_TOKENS,
    ExecutionMode,
    ExecutionModeNotImplemented,
    ReasoningMode,
    SYNVIOConfig,
)


def test_defaults_are_local_and_limited():
    config = SYNVIOConfig()
    assert config.execution_mode is ExecutionMode.LOCAL
    assert config.reasoning_mode is ReasoningMode.MEDIUM
    assert config.agent_steps_limit == 10
    # 4 tool calls per attempt is the minimum that fits read + write + test and
    # still leaves one call for the retry after a failing test.
    assert config.tool_execution_limit == 4
    assert config.max_repair_attempts == 2
    assert config.global_tool_limit == 8
    # A pinned engine keeps OpenJarvis from silently falling back to a cloud
    # provider when the local engine is down.
    assert config.engine == "ollama"


def test_engine_is_pinned_in_the_jarvis_config():
    jarvis_config = SYNVIOConfig(engine=" ollama ").to_jarvis_config()
    assert jarvis_config.engine.default == "ollama"


def test_empty_engine_leaves_selection_to_openjarvis():
    config = SYNVIOConfig(engine="")
    assert config.engine == ""
    assert config.to_jarvis_config().engine.default != ""


@pytest.mark.parametrize(
    ("mode", "expected_model", "expected_tokens"),
    [
        ("low", "gpt-4o-mini", 512),
        ("medium", "gpt-4o", 1024),
        ("high", "gpt-4o", 2048),
    ],
)
def test_reasoning_mode_selects_model_and_budget(mode, expected_model, expected_tokens):
    config = SYNVIOConfig(reasoning_mode=mode)
    assert config.reasoning_mode is ReasoningMode(mode)
    assert config.model == expected_model
    assert config.get_model() == expected_model
    assert config.max_tokens == expected_tokens
    assert config.max_tokens == DEFAULT_MAX_TOKENS[mode]


def test_model_override_wins_over_reasoning_mode():
    config = SYNVIOConfig(reasoning_mode="low", model_override="gpt-4o")
    assert config.model == "gpt-4o"


def test_custom_model_map_is_honoured():
    config = SYNVIOConfig(reasoning_mode="high", models={"high": "custom-model"})
    assert config.model == "custom-model"


@pytest.mark.parametrize("field", ["agent_steps_limit", "tool_execution_limit", "max_repair_attempts"])
def test_non_positive_limits_are_rejected(field):
    with pytest.raises(ValueError):
        SYNVIOConfig(**{field: 0})
    with pytest.raises(ValueError):
        SYNVIOConfig(**{field: -3})


def test_global_budgets_default_to_per_attempt_limit_times_attempts():
    config = SYNVIOConfig(
        agent_steps_limit=5, tool_execution_limit=4, max_repair_attempts=2
    )
    assert config.total_step_limit is None
    assert config.total_tool_limit is None
    # 5 steps and 4 tool calls per attempt, 2 attempts.
    assert config.global_step_limit == 10
    assert config.global_tool_limit == 8


def test_global_budgets_can_be_pinned_explicitly():
    config = SYNVIOConfig(
        agent_steps_limit=5,
        tool_execution_limit=4,
        max_repair_attempts=2,
        total_step_limit=7,
        total_tool_limit=6,
    )
    assert config.global_step_limit == 7
    assert config.global_tool_limit == 6
    # The per-attempt caps are untouched.
    assert config.agent_steps_limit == 5
    assert config.tool_execution_limit == 4


@pytest.mark.parametrize(
    "field", ["total_step_limit", "total_tool_limit", "max_test_runs"]
)
def test_non_positive_global_budgets_are_rejected(field):
    with pytest.raises(ValueError):
        SYNVIOConfig(**{field: 0})
    with pytest.raises(ValueError):
        SYNVIOConfig(**{field: -1})


def test_invalid_execution_mode_is_rejected():
    with pytest.raises(ValueError):
        SYNVIOConfig(execution_mode="mainframe")


def test_local_execution_is_supported():
    SYNVIOConfig(execution_mode="local").require_supported_execution()


@pytest.mark.parametrize("mode", ["cloud", "hybrid"])
def test_cloud_and_hybrid_are_not_implemented(mode):
    config = SYNVIOConfig(execution_mode=mode)
    with pytest.raises(ExecutionModeNotImplemented) as excinfo:
        config.require_supported_execution()
    assert mode in str(excinfo.value)
    assert isinstance(excinfo.value, NotImplementedError)


def test_jarvis_config_enforces_limits_and_model():
    config = SYNVIOConfig(
        reasoning_mode="high",
        agent_steps_limit=4,
        tool_execution_limit=7,
        model_override="gpt-4o",
    )
    jarvis_config = config.to_jarvis_config()
    assert jarvis_config.agent.max_turns == 4
    assert jarvis_config.intelligence.default_model == "gpt-4o"
    assert jarvis_config.intelligence.max_tokens == 2048
    assert jarvis_config.intelligence.temperature == config.temperature


def test_replace_returns_validated_copy():
    config = SYNVIOConfig()
    updated = config.replace(agent_steps_limit=2, tool_execution_limit=5)
    assert updated.agent_steps_limit == 2
    assert updated.tool_execution_limit == 5
    assert config.agent_steps_limit == 10
    with pytest.raises(ValueError):
        config.replace(agent_steps_limit=0)
