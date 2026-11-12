"""Compatibility checks against the installed OpenJarvis (pinned 1.0.5).

These tests fail loudly if the reused architecture (ReAct loop, tool executor,
config) changes shape, instead of letting SYNVIO silently drift.
"""

from __future__ import annotations

import inspect
import json
import re

import openjarvis
from openjarvis.agents.native_react import NativeReActAgent
from openjarvis.core.config import JarvisConfig
from openjarvis.core.types import ToolCall
from openjarvis.sdk import Jarvis
from openjarvis.tools._stubs import BaseTool, ToolExecutor

from synvio.autodev.tools import build_dev_tools
from synvio.autodev.workspace import Workspace


def test_openjarvis_version_is_supported():
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", openjarvis.__version__)
    assert match, openjarvis.__version__
    assert tuple(int(part) for part in match.groups()) >= (1, 0, 5)


def test_jarvis_sdk_surface_is_unchanged():
    params = set(inspect.signature(Jarvis.ask_full).parameters)
    assert {"model", "agent", "tools", "temperature", "max_tokens"} <= params


def test_native_react_accepts_max_turns():
    params = inspect.signature(NativeReActAgent.__init__).parameters
    assert "max_turns" in params
    assert "max_tokens" in params


def test_jarvis_config_enforces_max_turns():
    config = JarvisConfig()
    config.agent.max_turns = 3
    assert config.agent.max_turns == 3


def test_base_tool_contract_is_implemented_by_dev_tools(tmp_path):
    workspace = Workspace(tmp_path / "project")
    tools = build_dev_tools(workspace)
    assert len(tools) == 4
    for tool in tools:
        assert isinstance(tool, BaseTool)
        assert tool.spec.name.startswith("dev_")
        assert isinstance(tool.spec.parameters, dict)


def test_openjarvis_tool_executor_dispatches_dev_tools(tmp_path):
    workspace = Workspace(tmp_path / "project")
    executor = ToolExecutor(build_dev_tools(workspace))

    # OpenJarvis' ToolCall.arguments is a JSON *string*, not a dict.
    write = executor.execute(
        ToolCall(
            id="call-1",
            name="dev_write_file",
            arguments=json.dumps({"path": "mod.py", "content": "VALUE = 7\n"}),
        )
    )
    read = executor.execute(
        ToolCall(
            id="call-2",
            name="dev_read_file",
            arguments=json.dumps({"path": "mod.py"}),
        )
    )

    assert write.success is True
    assert read.success is True
    assert "VALUE = 7" in read.content
    assert workspace.read_text("mod.py") == "VALUE = 7\n"
