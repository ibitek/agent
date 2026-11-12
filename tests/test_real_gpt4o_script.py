"""Offline checks for the opt-in real-model script.

Nothing here touches an API: the paid path needs ``--confirm-paid`` plus a real
key, and every test below runs without either.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from synvio.autodev.runner import TestRunner as Runner  # alias: keep pytest from
from synvio.autodev.workspace import Workspace  # trying to collect the class

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "real_gpt4o_repair.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("real_gpt4o_repair", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_is_pinned_to_the_cloud_engine_and_gpt4o(script):
    config = script.build_config()

    assert config.engine == "cloud"
    assert config.provider == "openai"
    assert config.model == "gpt-4o"
    assert config.agent_steps_limit == 10
    assert config.tool_execution_limit == 8
    assert config.max_repair_attempts == 1
    assert config.global_step_limit == 10
    assert config.global_tool_limit == 8
    assert config.max_test_runs == 2

    # The pin also reaches the OpenJarvis config, so no fallback is possible.
    jarvis_config = config.to_jarvis_config()
    assert jarvis_config.engine.default == "cloud"
    assert jarvis_config.agent.max_turns == 10
    assert jarvis_config.intelligence.default_model == "gpt-4o"


def test_fixture_project_is_really_broken_and_the_fix_really_passes(script, tmp_path):
    script.build_project(tmp_path)
    workspace = Workspace(tmp_path)
    runner = Runner(workspace)

    broken = runner.run()
    assert broken.passed is False
    assert "2 failed" in broken.stdout

    fixed = script.BUGGY_SLUGIFY.replace('text.lower())', 'text.lower()).strip("-")')
    assert fixed != script.BUGGY_SLUGIFY
    workspace.write_text("slugify.py", fixed)

    repaired = runner.run()
    assert repaired.passed is True
    assert "3 passed" in repaired.stdout
    # The fix never touched the tests, so they stay a trustworthy oracle.
    assert script.fingerprint(tmp_path) == script.SLUGIFY_TESTS


def test_preflight_reports_the_missing_api_key(script, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    problems = script.preflight(script.build_config())

    assert any("OPENAI_API_KEY" in problem for problem in problems)


def test_dry_run_refuses_to_start_without_a_key(script, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert script.main([]) == 2


def test_dry_run_is_free_even_when_the_key_is_present(script, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    # Without --confirm-paid the script stops before any API call.
    assert script.main([]) == 0


def test_the_task_budget_allows_one_attempt_and_two_pytest_runs(script):
    """The paid run must stay inside: 10 steps, 1 attempt, 2 model test runs."""
    config = script.build_config()

    assert config.max_repair_attempts == 1
    assert config.max_test_runs == 2
    assert config.global_step_limit == config.agent_steps_limit == 10
    assert config.global_tool_limit == config.tool_execution_limit == 8

    # The budget must survive the measured worst case: 1 list + 2 reads +
    # 1 write + 2 pytest runs = 6 calls, leaving 4 spare for a further edit.
    worst_case_calls = 1 + 2 + 1 + 2
    assert config.global_tool_limit - worst_case_calls >= 2
    assert config.global_step_limit - worst_case_calls >= 2


def test_report_proves_the_engine_and_prints_real_usage(script, tmp_path, capsys):
    """The report must name the resolved engine and the measured token usage."""
    from types import SimpleNamespace

    from synvio.runtime import EngineIdentity

    script.build_project(tmp_path)
    report = SimpleNamespace(
        success=True,
        reason=script.REASON_TESTS_PASSED,
        attempts=1,
        steps_used=6,
        total_step_limit=10,
        tool_calls=5,
        total_tool_limit=8,
        tool_calls_denied=0,
        duration_seconds=9.4,
        model="gpt-4o",
        error=None,
        attempt_details=[
            {
                "attempt": 1,
                "turns": 6,
                "turns_allowed": 10,
                "tool_calls": 5,
                "step_limit_hit": False,
                "final_answer": "Fixed slugify().",
            }
        ],
        events=[],
        tests=[],
        files_modified=["slugify.py"],
        files_created=[],
        tokens={
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
        },
        workspace_root=str(tmp_path),
        log_path=str(tmp_path / ".synvio" / "execution.jsonl"),
    )
    # The real chain: OpenJarvis always wraps the resolved engine for telemetry.
    identity = EngineIdentity(
        chain=["InstrumentedEngine", "CloudEngine"], engine_id="cloud", is_cloud=True
    )

    script.print_report(report, tmp_path, script.SLUGIFY_TESTS, identity)

    out = capsys.readouterr().out
    # The wrapper must not hide the engine that actually answered.
    assert "InstrumentedEngine -> CloudEngine" in out
    assert "engine_id='cloud'" in out
    assert "is_cloud=True" in out
    assert "no local fallback" in out
    assert "prompt tokens     : 1200" in out
    assert "total tokens      : 1500" in out
    # Cost comes from OpenJarvis' own estimate_cost(), not a local table.
    assert "estimated cost    : $" in out


def test_a_local_engine_is_reported_as_an_invalid_run(script, tmp_path, capsys):
    """If OpenJarvis substituted a local engine, the report must say so."""
    from types import SimpleNamespace

    from synvio.runtime import EngineIdentity

    script.build_project(tmp_path)
    report = SimpleNamespace(
        success=True,
        reason=script.REASON_TESTS_PASSED,
        attempts=1,
        steps_used=1,
        total_step_limit=10,
        tool_calls=1,
        total_tool_limit=8,
        tool_calls_denied=0,
        duration_seconds=0.5,
        model="gpt-4o",
        error=None,
        attempt_details=[],
        events=[],
        tests=[],
        files_modified=[],
        files_created=[],
        tokens={},
        workspace_root=str(tmp_path),
        log_path=str(tmp_path / ".synvio" / "execution.jsonl"),
    )
    identity = EngineIdentity(
        chain=["InstrumentedEngine", "OllamaEngine"], engine_id="ollama", is_cloud=False
    )

    script.print_report(report, tmp_path, script.SLUGIFY_TESTS, identity)

    out = capsys.readouterr().out
    assert "LOCAL FALLBACK" in out
    assert "no local fallback" not in out
