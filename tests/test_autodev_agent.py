"""End-to-end tests for the autonomous developer agent (mocked model, real files)."""

from __future__ import annotations

import json
import sys

import pytest

from synvio.autodev.agent import (
    REASON_ATTEMPTS,
    REASON_GLOBAL_STEP_LIMIT,
    REASON_STEP_LIMIT,
    REASON_TESTS_PASSED,
    REASON_TOOL_LIMIT,
    DeveloperAgent,
)
from synvio.autodev.tools import (
    DevListFilesTool,
    DevRunTestsTool,
    DevWriteFileTool,
)
from synvio.autodev.workspace import Workspace
from synvio.config import ExecutionModeNotImplemented, SYNVIOConfig

from support import (
    BROKEN_PROJECT,
    FIXED_CALCULATOR,
    RepairBotEngine,
    ScriptedEngine,
    react_action,
    react_final,
)


def make_agent(workspace, engine, **config_overrides):
    config = SYNVIOConfig(
        **{
            "agent_steps_limit": 6,
            "tool_execution_limit": 8,
            "max_repair_attempts": 2,
            **config_overrides,
        }
    )
    return DeveloperAgent(engine=engine, workspace=workspace, config=config)


def test_agent_fixes_the_bug_and_tests_really_pass(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.write_text("calculator.py", BROKEN_PROJECT["calculator.py"])
    workspace.write_text("test_calculator.py", BROKEN_PROJECT["test_calculator.py"])
    engine = RepairBotEngine()

    report = make_agent(workspace, engine).fix("The calculator test is failing. Fix it.")

    # The real file on disk was rewritten.
    assert "return a + b" in workspace.read_text("calculator.py")
    assert "return a - b" not in workspace.read_text("calculator.py")

    # The real test process ran and really passed.
    assert report.success is True
    assert report.reason == REASON_TESTS_PASSED
    assert report.attempts == 1
    assert report.final_test is not None
    assert report.final_test["passed"] is True
    assert report.final_test["command"][0] == sys.executable
    assert "passed" in report.final_test["stdout_tail"].lower()

    # Limits and bookkeeping are reported truthfully.
    assert report.steps_used <= report.step_limit
    assert report.tool_calls >= 3
    assert report.files_created == []
    assert report.files_modified == ["calculator.py"]
    assert report.error is None
    assert engine.calls  # the mocked model was actually consulted


def test_agent_writes_structured_execution_log(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.write_text("calculator.py", BROKEN_PROJECT["calculator.py"])
    workspace.write_text("test_calculator.py", BROKEN_PROJECT["test_calculator.py"])

    make_agent(workspace, RepairBotEngine()).fix("Fix the failing test.")

    log_path = workspace.root / ".synvio" / "execution.jsonl"
    assert log_path.exists()
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = [record["event"] for record in records]

    assert events[0] == "task_start"
    assert "attempt_start" in events
    assert "tool_call" in events
    assert "test_run" in events
    assert events[-1] == "task_end"

    tool_events = [r for r in records if r["event"] == "tool_call"]
    assert tool_events[0]["tool"] == "dev_read_file"
    assert any(r["tool"] == "dev_write_file" and r["success"] for r in tool_events)
    assert any(r["tool"] == "dev_run_tests" for r in tool_events)


def test_report_is_json_serialisable(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.write_text("calculator.py", BROKEN_PROJECT["calculator.py"])
    workspace.write_text("test_calculator.py", BROKEN_PROJECT["test_calculator.py"])

    report = make_agent(workspace, RepairBotEngine()).fix("Fix it.")
    payload = json.loads(report.to_json())

    assert payload["success"] is True
    assert payload["reason"] == REASON_TESTS_PASSED
    assert payload["limits"]["steps_per_attempt"] == 6
    assert payload["limits"]["tool_calls_per_attempt"] == 8
    assert payload["limits"]["max_repair_attempts"] == 2
    assert payload["limits"]["global_steps"] == 12
    assert payload["limits"]["global_tool_calls"] == 16
    assert payload["tokens"]["total_tokens"] > 0
    assert payload["attempt_details"][0]["turns"] >= 1
    assert payload["final_test"]["passed"] is True


def test_unfixed_bug_exhausts_repair_attempts(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.write_text("calculator.py", BROKEN_PROJECT["calculator.py"])
    workspace.write_text("test_calculator.py", BROKEN_PROJECT["test_calculator.py"])
    engine = ScriptedEngine([react_final("Looks fine to me, done.")])

    report = make_agent(workspace, engine, max_repair_attempts=2).fix("Fix it.")

    assert report.success is False
    assert report.reason == REASON_ATTEMPTS
    assert report.attempts == 2
    assert len(report.tests) == 2
    assert all(test["passed"] is False for test in report.tests)
    assert report.files_modified == []
    assert "return a - b" in workspace.read_text("calculator.py")


def test_tool_limit_stops_the_loop(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.write_text("calculator.py", BROKEN_PROJECT["calculator.py"])
    engine = ScriptedEngine([react_action("dev_run_tests")] * 5)

    report = make_agent(
        workspace,
        engine,
        agent_steps_limit=5,
        tool_execution_limit=1,
        max_repair_attempts=1,
    ).fix("Fix it.")

    assert report.success is False
    assert report.reason == REASON_TOOL_LIMIT
    assert report.tool_calls == 1
    assert report.attempts == 1


def test_model_may_run_the_tests_twice_at_most(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.write_text("calculator.py", BROKEN_PROJECT["calculator.py"])
    workspace.write_text("test_calculator.py", BROKEN_PROJECT["test_calculator.py"])
    # Edit first: dev_run_tests refuses to run before any code change.
    engine = ScriptedEngine(
        [
            react_action(
                "dev_write_file",
                path="calculator.py",
                content=BROKEN_PROJECT["calculator.py"] + "\n# still broken\n",
            ),
            react_action("dev_run_tests"),
            react_action("dev_run_tests"),
            react_action("dev_run_tests"),  # refused: the cap is two
            react_final("Done."),
        ]
    )

    report = make_agent(
        workspace,
        engine,
        agent_steps_limit=5,
        tool_execution_limit=5,
        max_repair_attempts=1,
        max_test_runs=2,
    ).fix("Fix it.")

    calls = [event for event in report.events if event["event"] == "tool_call"]
    assert calls[0]["tool"] == "dev_write_file"
    assert calls[0]["success"] is True
    assert calls[1]["tool"] == "dev_run_tests"
    assert calls[1]["success"] is True
    assert calls[2]["tool"] == "dev_run_tests"
    assert calls[2]["success"] is True
    # The third real pytest invocation is refused: two runs per task, not more.
    assert calls[3]["success"] is False
    assert "test-run budget" in calls[3]["output"].lower()
    # SYNVIO's own verification is separate and still ran exactly once.
    assert len(report.tests) == 1



def test_step_limit_stops_the_loop(tmp_path):
    workspace = Workspace(tmp_path / "project")
    engine = ScriptedEngine([react_action("dev_list_files")] * 5)

    report = make_agent(
        workspace,
        engine,
        agent_steps_limit=1,
        tool_execution_limit=5,
        max_repair_attempts=1,
    ).fix("Fix it.")

    assert report.success is False
    assert report.reason == REASON_STEP_LIMIT
    assert report.steps_used == 1


def test_agent_cannot_write_outside_workspace(tmp_path):
    workspace = Workspace(tmp_path / "project")
    outside = tmp_path / "pwned.txt"
    engine = ScriptedEngine(
        [react_action("dev_write_file", path="../pwned.txt", content="pwned")]
    )

    report = make_agent(
        workspace, engine, tool_execution_limit=2, max_repair_attempts=1
    ).fix("Escape the sandbox.")

    assert not outside.exists()
    assert report.files_modified == []
    tool_events = [e for e in report.events if e["event"] == "tool_call"]
    assert tool_events[0]["success"] is False
    assert "escape" in tool_events[0]["output"].lower()


def test_tool_budget_is_not_refreshed_by_a_repair_attempt(tmp_path):
    """One shared tool budget: attempt 2 cannot spend a fresh allowance."""
    workspace = Workspace(tmp_path / "project")
    workspace.write_text("calculator.py", BROKEN_PROJECT["calculator.py"])
    engine = ScriptedEngine([react_action("dev_run_tests")] * 8)

    report = make_agent(
        workspace,
        engine,
        agent_steps_limit=2,
        tool_execution_limit=2,
        max_repair_attempts=2,
        total_tool_limit=3,
    ).fix("Fix it.")

    assert report.success is False
    assert report.reason == REASON_TOOL_LIMIT
    assert report.attempts == 2
    # A per-attempt budget would have allowed 2 + 2 calls; the shared one caps
    # the whole task at 3 and reports the refusal the model received.
    assert report.tool_calls == 3
    assert report.tool_calls_denied == 1
    assert report.total_tool_limit == 3


def test_global_step_budget_stops_before_the_next_attempt(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.write_text("calculator.py", BROKEN_PROJECT["calculator.py"])
    engine = ScriptedEngine([react_action("dev_list_files")] * 8)

    report = make_agent(
        workspace,
        engine,
        agent_steps_limit=3,
        tool_execution_limit=5,
        max_repair_attempts=3,
        total_step_limit=3,
    ).fix("Fix it.")

    assert report.success is False
    assert report.reason == REASON_GLOBAL_STEP_LIMIT
    assert report.steps_used == 3
    assert report.attempts == 1  # the second attempt was never started
    assert report.total_step_limit == 3
    assert any(e["event"] == "global_step_limit_reached" for e in report.events)


def test_report_aggregates_token_usage(tmp_path):
    workspace = Workspace(tmp_path / "project")
    workspace.write_text("calculator.py", BROKEN_PROJECT["calculator.py"])
    workspace.write_text("test_calculator.py", BROKEN_PROJECT["test_calculator.py"])
    engine = RepairBotEngine()

    report = make_agent(workspace, engine).fix("Fix it.")

    assert report.tokens["prompt_tokens"] == 10 * len(engine.calls)
    assert report.tokens["completion_tokens"] == 5 * len(engine.calls)
    assert report.tokens["total_tokens"] == 15 * len(engine.calls)


def test_agent_refuses_cloud_execution(tmp_path):
    workspace = Workspace(tmp_path / "project")
    with pytest.raises(ExecutionModeNotImplemented):
        make_agent(
            workspace,
            ScriptedEngine([react_final()]),
            execution_mode="cloud",
        ).fix("Fix it.")


def test_agent_reports_engine_errors(tmp_path):
    class ExplodingEngine:
        _publishes_events = True

        def generate(self, *args, **kwargs):
            raise RuntimeError("provider exploded")

    workspace = Workspace.from_files(BROKEN_PROJECT)
    report = make_agent(workspace, ExplodingEngine()).fix("Fix it.")

    assert report.success is False
    assert report.reason == "agent_error"
    assert "provider exploded" in (report.error or "")
    assert report.final_test is None


# -- regression: the first real GPT-4o run -------------------------------
#
# The paid run spent every allowed call before editing anything:
#
#   1 dev_list_files  2 dev_read_file  3 dev_read_file  4 dev_run_tests
#   ... no budget left for dev_write_file -> tool_execution_limit_reached
#
# These tests replay that exact trace offline and pin the fix: a test run
# before the first edit is refused (and costs no pytest run), so the same
# trace still reaches the edit and the verification.


def test_the_observed_gpt4o_trace_still_reaches_the_edit(tmp_path):
    workspace = Workspace.from_files(BROKEN_PROJECT)
    engine = ScriptedEngine(
        [
            react_action("dev_list_files", pattern="**/*"),
            react_action("dev_read_file", path="calculator.py"),
            react_action("dev_read_file", path="test_calculator.py"),
            react_action("dev_run_tests"),  # refused: nothing edited yet
            react_action(
                "dev_write_file", path="calculator.py", content=FIXED_CALCULATOR
            ),
            react_action("dev_run_tests"),  # real run, really passes
            react_final("Fixed add()."),
        ]
    )

    report = make_agent(
        workspace,
        engine,
        agent_steps_limit=10,
        tool_execution_limit=8,
        max_repair_attempts=1,
        max_test_runs=2,
    ).fix("Fix it.")

    calls = [event for event in report.events if event["event"] == "tool_call"]
    assert [call["tool"] for call in calls] == [
        "dev_list_files",
        "dev_read_file",
        "dev_read_file",
        "dev_run_tests",
        "dev_write_file",
        "dev_run_tests",
    ]
    # The wasted test run is refused with an actionable message...
    assert calls[3]["success"] is False
    assert "dev_write_file" in calls[3]["output"]
    # ...and it did not consume a pytest run, so the real verification still ran.
    assert calls[5]["success"] is True
    assert report.success is True
    assert report.reason == REASON_TESTS_PASSED
    assert report.files_modified == ["calculator.py"]
    assert "return a + b" in workspace.read_text("calculator.py")
    # Only SYNVIO's own ground-truth run is recorded: a refusal is not a run.
    assert len(report.tests) == 1


def test_a_test_run_before_any_edit_is_refused_without_spending_a_run(tmp_path):
    workspace = Workspace.from_files(BROKEN_PROJECT)
    # The agent baselines mutations at task start; do the same here.
    workspace.clear_mutations()
    tool = DevRunTestsTool(workspace, max_runs=2)

    refused = tool.execute()

    assert refused.success is False
    assert "dev_write_file" in refused.content
    assert tool.runs == 0  # nothing was executed
    assert tool.last_outcome is None

    workspace.write_text("calculator.py", FIXED_CALCULATOR)
    ok = tool.execute()

    assert ok.success is True
    assert tool.runs == 1


def test_rewriting_identical_content_is_rejected_as_a_no_op(tmp_path):
    workspace = Workspace.from_files(BROKEN_PROJECT)
    workspace.clear_mutations()
    tool = DevWriteFileTool(workspace)

    result = tool.execute(path="calculator.py", content=BROKEN_PROJECT["calculator.py"])

    assert result.success is False
    assert "no change" in result.content.lower()
    # A rejected no-op is not an edit, so the test gate stays closed.
    assert workspace.files_modified == []


def test_listing_hides_synvio_artifacts(tmp_path):
    workspace = Workspace.from_files(BROKEN_PROJECT)
    workspace.write_text(".synvio/report.json", "{}")

    listing = DevListFilesTool(workspace).execute(pattern="**/*")

    assert listing.success is True
    assert ".synvio" not in listing.content
    assert "calculator.py" in listing.content


def test_the_prompt_states_the_real_budget_and_edit_priority(tmp_path):
    workspace = Workspace.from_files(BROKEN_PROJECT)
    engine = ScriptedEngine([react_final()])

    make_agent(
        workspace,
        engine,
        agent_steps_limit=10,
        tool_execution_limit=8,
        max_repair_attempts=1,
    ).fix("Fix it.")

    prompt = "\n".join(
        str(getattr(message, "content", "")) for message in engine.calls[0]["messages"]
    )
    assert "## Budget for this attempt" in prompt
    assert "steps available      : 10" in prompt
    assert "tool calls available : 8" in prompt
    # The instructions must say what the paid run got wrong: edit, then test.
    assert "dev_run_tests refuses to run until a file has been changed" in prompt
    assert "By your third tool call you should already have written a fix." in prompt


def test_repair_attempts_see_the_remaining_budget_not_a_fresh_one(tmp_path):
    workspace = Workspace.from_files(BROKEN_PROJECT)
    still_broken = BROKEN_PROJECT["calculator.py"] + "\n# nope\n"
    engine = ScriptedEngine(
        [
            react_action("dev_write_file", path="calculator.py", content=still_broken),
            react_action("dev_run_tests"),
            react_action(
                "dev_write_file", path="calculator.py", content=FIXED_CALCULATOR
            ),
            react_action("dev_run_tests"),
            react_final("Fixed."),
        ]
    )

    report = make_agent(
        workspace,
        engine,
        agent_steps_limit=2,
        tool_execution_limit=2,
        max_repair_attempts=2,
    ).fix("Fix it.")

    assert report.success is True
    assert report.attempts == 2
    second_prompt = "\n".join(
        str(getattr(message, "content", "")) for message in engine.calls[-1]["messages"]
    )
    assert "## Repair attempt 2" in second_prompt
    # Attempt 2 is told what is left (4 - 2 = 2), not a fresh allowance.
    assert "tool calls available : 2" in second_prompt
