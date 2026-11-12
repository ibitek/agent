"""SYNVIO autonomous developer agent demo.

The *model* is mocked (no network, no API key). Everything else is real: the
files live on disk, the OpenJarvis ReAct loop drives the tool calls, the tools
edit real files and pytest runs in a real subprocess.

Run:
    python examples/bugfix_demo.py
    python examples/bugfix_demo.py --keep
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from synvio.autodev import DeveloperAgent, Workspace  # noqa: E402
from synvio.config import SYNVIOConfig  # noqa: E402

BROKEN_CALCULATOR = '''"""Tiny calculator module."""


def add(a, b):
    """Return the sum of a and b."""
    return a - b  # BUG: subtraction instead of addition
'''

TEST_FILE = '''from calculator import add


def test_add_positive():
    assert add(2, 3) == 5


def test_add_zero():
    assert add(0, 0) == 0
'''

FIXED_CALCULATOR = '''"""Tiny calculator module."""


def add(a, b):
    """Return the sum of a and b."""
    return a + b
'''


def react_action(tool: str, **arguments: Any) -> str:
    return (
        f"Thought: I need to use {tool}\n"
        f"Action: {tool}\n"
        f"Action Input: {json.dumps(arguments)}"
    )


def last_observation(messages: List[Any]) -> str:
    for message in reversed(messages):
        role = getattr(message.role, "value", message.role)
        content = str(getattr(message, "content", ""))
        if role == "user" and content.startswith("Observation:"):
            return content
    return ""


class MockedModel:
    """Scripted stand-in for an LLM: same protocol, zero network calls."""

    engine_id = "demo-mocked-model"
    _publishes_events = True

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, **kwargs) -> Dict[str, Any]:
        self.calls += 1
        observation = last_observation(messages)
        if not observation:
            content = react_action("dev_read_file", path="calculator.py")
        elif "return a - b" in observation:
            content = react_action(
                "dev_write_file", path="calculator.py", content=FIXED_CALCULATOR
            )
        elif "PASSED (" in observation:
            content = "Thought: the tests pass now.\nFinal Answer: FIXED"
        else:
            content = react_action("dev_run_tests")
        return {
            "content": content,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    def list_models(self) -> List[str]:
        return ["mocked"]

    def close(self) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Keep the workspace.")
    args = parser.parse_args()

    workspace = Workspace.from_files(
        {"calculator.py": BROKEN_CALCULATOR, "test_calculator.py": TEST_FILE}
    )
    print(f"workspace : {workspace.root}")
    print(f"before    : {workspace.read_text('calculator.py').splitlines()[-1].strip()}")

    config = SYNVIOConfig(
        agent_steps_limit=6,
        tool_execution_limit=8,
        max_repair_attempts=2,
    )
    model = MockedModel()
    agent = DeveloperAgent(
        engine=model, workspace=workspace, config=config, model="gpt-4o-mini"
    )
    report = agent.fix("The calculator tests fail. Diagnose and fix the bug.")

    print(f"after     : {workspace.read_text('calculator.py').splitlines()[-1].strip()}")
    print(f"success   : {report.success}")
    print(f"reason    : {report.reason}")
    print(f"attempts  : {report.attempts}")
    print(f"steps     : {report.steps_used} (limit {report.step_limit}/attempt)")
    print(f"tool calls: {report.tool_calls} (limit {report.tool_limit}/attempt)")
    print(f"modified  : {report.files_modified}")
    print(f"log       : {report.log_path}")
    if report.final_test:
        print("--- real pytest output ---")
        print(report.final_test["stdout_tail"].strip())
        print(f"exit code : {report.final_test['returncode']}")

    if args.keep:
        print("workspace kept on disk")
    else:
        workspace.cleanup()
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
