"""Test doubles and fixtures for SYNVIO tests.

No network, no API keys: ``ScriptedEngine`` and ``RepairBotEngine`` implement
just the ``generate()`` contract the OpenJarvis ReAct agent needs.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

BROKEN_CALCULATOR = '''"""Tiny calculator module."""


def add(a, b):
    """Return the sum of a and b."""
    return a - b  # BUG: subtraction instead of addition
'''

FIXED_CALCULATOR = '''"""Tiny calculator module."""


def add(a, b):
    """Return the sum of a and b."""
    return a + b
'''

CALCULATOR_TESTS = '''from calculator import add


def test_add_positive():
    assert add(2, 3) == 5


def test_add_zero():
    assert add(0, 0) == 0
'''

BROKEN_PROJECT: Dict[str, str] = {
    "calculator.py": BROKEN_CALCULATOR,
    "test_calculator.py": CALCULATOR_TESTS,
}


def react_action(tool: str, **arguments: Any) -> str:
    return (
        f"Thought: I will use {tool}\n"
        f"Action: {tool}\n"
        f"Action Input: {json.dumps(arguments)}"
    )


def react_final(answer: str = "DONE") -> str:
    return f"Thought: finished\nFinal Answer: {answer}"


def last_observation(messages: List[Any]) -> str:
    for message in reversed(messages):
        role = getattr(message.role, "value", message.role)
        content = str(getattr(message, "content", ""))
        if role == "user" and content.startswith("Observation:"):
            return content
    return ""


class ScriptedEngine:
    """Deterministic engine double returning canned responses in order."""

    engine_id = "scripted"
    _publishes_events = True

    def __init__(self, responses: Optional[List[str]] = None) -> None:
        self.responses: List[str] = list(responses or [react_final()])
        self.calls: List[Dict[str, Any]] = []

    def generate(
        self,
        messages: List[Any],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        index = len(self.calls)
        self.calls.append(
            {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": list(messages),
                "kwargs": kwargs,
            }
        )
        if index < len(self.responses):
            content = self.responses[index]
        elif self.responses:
            content = self.responses[-1]
        else:  # pragma: no cover - defensive
            content = react_final()
        return {
            "content": content,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

    def list_models(self) -> List[str]:
        return ["scripted-model"]

    def close(self) -> None:  # pragma: no cover - parity with real engines
        pass


class RepairBotEngine(ScriptedEngine):
    """Mocked model that really reacts to tool observations.

    It reads a file, detects the known bug, rewrites the file and then runs the
    tests until it observes a real pass. The model is scripted; every tool call
    it requests is executed for real by SYNVIO.
    """

    def __init__(
        self,
        filename: str = "calculator.py",
        *,
        fixed_source: str = FIXED_CALCULATOR,
        bug_marker: str = "return a - b",
    ) -> None:
        super().__init__([])
        self.filename = filename
        self.fixed_source = fixed_source
        self.bug_marker = bug_marker

    def generate(
        self,
        messages: List[Any],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        observation = last_observation(messages)
        if not observation:
            content = react_action("dev_read_file", path=self.filename)
        elif self.bug_marker in observation:
            content = react_action(
                "dev_write_file", path=self.filename, content=self.fixed_source
            )
        elif "PASSED (" in observation:
            content = react_final("Fixed the failing calculator test.")
        else:
            content = react_action("dev_run_tests")

        self.calls.append(
            {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": list(messages),
            }
        )
        return {
            "content": content,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }


class RecordingTool(BaseTool):
    """Minimal workspace-free tool used to test budgets and step limits."""

    is_local = True

    def __init__(self, name: str = "ping") -> None:
        self.tool_id = name
        self._name = name
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self._name,
            description="Test tool that returns pong.",
            parameters={"type": "object", "properties": {}, "required": []},
            category="test",
        )

    def execute(self, **params: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_name=self._name, content="pong", success=True)
