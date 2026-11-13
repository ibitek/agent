"""Autonomous developer agent.

The reasoning loop is OpenJarvis' ``NativeReActAgent`` (see
:func:`synvio.runtime.run_agent`). SYNVIO adds only what OpenJarvis leaves to
the caller:

* a workspace path jail for every file operation,
* allowlisted test execution with a sanitized environment,
* hard limits (steps per attempt, tool executions, repair attempts) where the
  tool budget and the step budget are *task-global*: a retry never resets them,
* a ground-truth verification step: SYNVIO runs the tests itself after every
  attempt instead of trusting the model's claim that they pass,
* structured JSON-lines logging and a final :class:`TaskReport`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from synvio.autodev.runner import TestOutcome, TestRunner
from synvio.autodev.tools import build_dev_tools
from synvio.autodev.workspace import Workspace
from synvio.config import SYNVIOConfig
from synvio.runtime import BudgetedTool, ToolBudget, run_agent

#: Reason codes reported when a run does not end with green tests.
REASON_TESTS_PASSED = "tests_passed"
REASON_TOOL_LIMIT = "tool_execution_limit_reached"
REASON_STEP_LIMIT = "agent_step_limit_reached"
REASON_GLOBAL_STEP_LIMIT = "global_step_limit_reached"
REASON_ATTEMPTS = "max_repair_attempts_reached"
REASON_ERROR = "agent_error"

DEV_INSTRUCTIONS = """\
You are SYNVIO's developer agent working inside a dedicated project workspace.
Default to English unless the user explicitly requests another language.

Available tools: dev_list_files, dev_read_file, dev_write_file, dev_run_tests.

Work in this priority order and never skip step 2:
1. DIAGNOSE cheaply. One dev_list_files is enough to see the layout; then read
   only the file that must change and the test that fails. Never list twice and
   never re-read a file you have already seen.
2. EDIT the code with dev_write_file, passing the COMPLETE new file content.
   Nothing else can make the task succeed, so do this before running tests.
3. VERIFY with dev_run_tests and read the REAL output it returns.
4. If tests still fail, edit again and re-run. When they pass, answer with
   "Final Answer: DONE".

Budget rules (hard limits enforced by SYNVIO, not suggestions):
- One shared budget of tool calls covers the whole task: listing, reading and
  testing all come out of it. Spending it on discovery leaves nothing for the fix.
- By your third tool call you should already have written a fix.
- dev_run_tests refuses to run until a file has been changed, and the number of
  real pytest runs is capped. Do not run the same unchanged code twice.
- A write that does not change the file is rejected as a no-op.
- You cannot use the shell, the network, or any path outside the workspace, so
  guessing about unseen files is wasted work: read only what you need.

Use exactly one Action per step, in this format:

Thought: <reasoning>
Action: <tool_name>
Action Input: <json object>

Paths are always relative to the workspace root; absolute paths and '..' are
rejected.
"""


def budget_brief(steps: int, tool_calls: int) -> str:
    """Short, truthful statement of what is left before an attempt starts."""
    return (
        "## Budget for this attempt\n"
        f"steps available      : {steps}\n"
        f"tool calls available : {tool_calls}\n"
        "dev_run_tests only runs after dev_write_file has changed the code, and\n"
        "each real pytest run is counted against the task budget."
    )


@dataclass
class TaskReport:
    """Structured result of one developer-agent task."""

    task: str
    success: bool
    reason: str
    attempts: int
    steps_used: int
    tool_calls: int
    step_limit: int
    tool_limit: int
    max_attempts: int
    total_step_limit: int = 0
    total_tool_limit: int = 0
    files_created: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    tests: List[Dict[str, Any]] = field(default_factory=list)
    final_test: Optional[Dict[str, Any]] = None
    workspace_root: str = ""
    log_path: str = ""
    model: str = ""
    duration_seconds: float = 0.0
    error: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    tokens: Dict[str, int] = field(default_factory=dict)
    tool_calls_denied: int = 0
    attempt_details: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "success": self.success,
            "reason": self.reason,
            "attempts": self.attempts,
            "steps_used": self.steps_used,
            "tool_calls": self.tool_calls,
            "limits": {
                "steps_per_attempt": self.step_limit,
                "tool_calls_per_attempt": self.tool_limit,
                "max_repair_attempts": self.max_attempts,
                "global_steps": self.total_step_limit,
                "global_tool_calls": self.total_tool_limit,
            },
            "tokens": self.tokens,
            "tool_calls_denied": self.tool_calls_denied,
            "attempt_details": self.attempt_details,
            "files_created": self.files_created,
            "files_modified": self.files_modified,
            "tests": self.tests,
            "final_test": self.final_test,
            "workspace_root": self.workspace_root,
            "log_path": self.log_path,
            "model": self.model,
            "duration_seconds": round(self.duration_seconds, 3),
            "error": self.error,
            "events": self.events,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


class DeveloperAgent:
    """Runs a bug-fixing task with hard limits and real test verification."""

    def __init__(
        self,
        *,
        engine: Any,
        workspace: Optional[Workspace] = None,
        config: Optional[SYNVIOConfig] = None,
        model: Optional[str] = None,
        test_runner: Optional[Any] = None,
        log_name: str = "execution.jsonl",
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.workspace = workspace or Workspace()
        # Defaults follow the global SYNVIO policy rather than inventing a
        # looser budget here: 10 steps and 4 tool calls per attempt, 2 attempts.
        self.config = config or SYNVIOConfig()
        self.engine = engine
        self.model = model or self.config.model
        self.log_path = self.workspace.root / ".synvio" / log_name
        self._runner = test_runner or TestRunner(
            self.workspace, timeout=self.config.test_timeout_seconds
        )
        self._events: List[Dict[str, Any]] = []
        self._on_event = on_event

    # -- logging -----------------------------------------------------------

    def _log(self, event: str, **fields: Any) -> None:
        record = {"ts": round(time.time(), 3), "event": event, **fields}
        self._events.append(record)
        if self._on_event is not None:
            try:
                self._on_event(dict(record))
            except Exception:  # event sinks must never break the repair task
                pass
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except OSError:  # logging must never break the task
            pass

    def _tool_event(self, payload: Dict[str, Any]) -> None:
        self._log(payload.pop("event", "tool_call"), **payload)

    # -- prompting ---------------------------------------------------------

    @staticmethod
    def _prompt(
        task: str, *, steps: int, tool_calls: int, repair_note: str = ""
    ) -> str:
        """Instructions + the true remaining budget + optional repair context."""
        parts = [DEV_INSTRUCTIONS, budget_brief(steps, tool_calls)]
        if repair_note:
            parts.append(repair_note)
        parts.append(f"## Task\n{task}")
        return "\n\n".join(parts)

    @staticmethod
    def _repair_note(attempt: int, outcome: TestOutcome) -> str:
        return (
            f"## Repair attempt {attempt + 1}\n"
            "The previous attempt did not make the tests pass. Real test output:\n\n"
            f"```\n{outcome.failure_excerpt()}\n```\n\n"
            "Fix the code with dev_write_file and run dev_run_tests again."
        )

    # -- main loop ---------------------------------------------------------

    def fix(self, task: str, *, test_target: Optional[str] = None) -> TaskReport:
        """Diagnose and repair a project until tests pass or a limit is hit."""
        started = time.monotonic()
        self.config.require_supported_execution()
        self._log(
            "task_start",
            task=task,
            model=self.model,
            execution_mode=self.config.execution_mode.value,
            reasoning_mode=self.config.reasoning_mode.value,
            limits={
                "steps_per_attempt": self.config.agent_steps_limit,
                "tool_calls_per_attempt": self.config.tool_execution_limit,
                "max_repair_attempts": self.config.max_repair_attempts,
                "global_steps": self.config.global_step_limit,
                "global_tool_calls": self.config.global_tool_limit,
            },
            workspace=str(self.workspace.root),
        )

        # The prompt is rebuilt for every attempt so it can state the *real*
        # remaining budget instead of a number the model has already outgrown.
        repair_note = ""
        # Report only the changes this task makes, not files that were already
        # written into the workspace by the caller.
        self.workspace.clear_mutations()
        tests: List[Dict[str, Any]] = []
        attempt_details: List[Dict[str, Any]] = []
        steps_used = 0
        tokens: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        attempts = 0
        success = False
        error: Optional[str] = None
        reason = REASON_ATTEMPTS

        global_step_limit = self.config.global_step_limit
        # Exactly one tool budget for the whole task: a retry must not refresh
        # it, otherwise max_repair_attempts would silently multiply the limit.
        budget = ToolBudget(self.config.global_tool_limit)

        for attempt in range(1, self.config.max_repair_attempts + 1):
            remaining_steps = global_step_limit - steps_used
            if remaining_steps < 1:
                reason = REASON_GLOBAL_STEP_LIMIT
                self._log(
                    "global_step_limit_reached",
                    attempt=attempt,
                    steps_used=steps_used,
                    global_step_limit=global_step_limit,
                )
                break

            attempts = attempt
            turns_allowed = min(self.config.agent_steps_limit, remaining_steps)
            tools = [
                BudgetedTool(tool, budget, on_event=self._tool_event)
                for tool in build_dev_tools(
                    self.workspace, self._runner, max_test_runs=self.config.max_test_runs
                )
            ]
            tool_calls_before = budget.used
            prompt = self._prompt(
                task,
                steps=turns_allowed,
                tool_calls=budget.remaining,
                repair_note=repair_note,
            )
            self._log(
                "attempt_start",
                attempt=attempt,
                turns_allowed=turns_allowed,
                remaining_steps=remaining_steps,
                remaining_tool_calls=budget.remaining,
            )

            try:
                result = run_agent(
                    task=prompt,
                    engine=self.engine,
                    model=self.model,
                    tools=tools,
                    max_turns=turns_allowed,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                )
            except Exception as exc:  # engine/agent failure is a terminal state
                error = f"{type(exc).__name__}: {exc}"
                self._log("agent_error", attempt=attempt, error=error)
                reason = REASON_ERROR
                break

            metadata = getattr(result, "metadata", None) or {}
            turns = int(getattr(result, "turns", 0) or 0)
            steps_used += turns
            for key in tokens:
                tokens[key] += int(metadata.get(key, 0) or 0)
            step_limit_hit = bool(metadata.get("max_turns_exceeded"))
            detail: Dict[str, Any] = {
                "attempt": attempt,
                "turns": turns,
                "turns_allowed": turns_allowed,
                "tool_calls": budget.used - tool_calls_before,
                "tool_calls_total": budget.used,
                "steps_total": steps_used,
                "step_limit_hit": step_limit_hit,
                "final_answer": (getattr(result, "content", "") or "")[:2000],
            }
            attempt_details.append(detail)
            self._log(
                "attempt_end", tool_calls_denied=budget.denied, **detail
            )

            outcome = self._runner.run(test_target)
            tests.append(outcome.to_dict())
            self._log(
                "test_run",
                attempt=attempt,
                **outcome.to_dict(),
            )

            if outcome.passed:
                success = True
                reason = REASON_TESTS_PASSED
                break
            if budget.exhausted:
                reason = REASON_TOOL_LIMIT
                break
            if step_limit_hit:
                reason = REASON_STEP_LIMIT
            else:
                reason = REASON_ATTEMPTS
            if attempt < self.config.max_repair_attempts:
                repair_note = self._repair_note(attempt, outcome)

        final_test = tests[-1] if tests else None
        report = TaskReport(
            task=task,
            success=success,
            reason=reason,
            attempts=attempts,
            steps_used=steps_used,
            tool_calls=budget.used,
            step_limit=self.config.agent_steps_limit,
            tool_limit=self.config.tool_execution_limit,
            max_attempts=self.config.max_repair_attempts,
            total_step_limit=global_step_limit,
            total_tool_limit=self.config.global_tool_limit,
            files_created=self.workspace.files_created,
            files_modified=self.workspace.files_modified,
            tests=tests,
            final_test=final_test,
            workspace_root=str(self.workspace.root),
            log_path=str(self.log_path),
            model=self.model,
            duration_seconds=time.monotonic() - started,
            error=error,
            tokens=tokens,
            tool_calls_denied=budget.denied,
            attempt_details=attempt_details,
        )
        self._log(
            "task_end",
            success=report.success,
            reason=report.reason,
            attempts=report.attempts,
            steps_used=report.steps_used,
            tool_calls=report.tool_calls,
            tool_calls_denied=report.tool_calls_denied,
            tokens=report.tokens,
        )
        report.events = list(self._events)
        return report


__all__ = [
    "DEV_INSTRUCTIONS",
    "DeveloperAgent",
    "REASON_ATTEMPTS",
    "REASON_ERROR",
    "REASON_GLOBAL_STEP_LIMIT",
    "REASON_STEP_LIMIT",
    "REASON_TESTS_PASSED",
    "REASON_TOOL_LIMIT",
    "TaskReport",
    "budget_brief",
]
