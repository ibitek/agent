"""CLI tests: real command wiring, no inference engine and no network."""

from __future__ import annotations

import contextlib
import json

from click.testing import CliRunner

from synvio import __version__
from synvio import cli as cli_module
from synvio.autodev.workspace import Workspace

from support import BROKEN_PROJECT, RepairBotEngine, ScriptedEngine, react_final


def run(*args: str):
    return CliRunner().invoke(cli_module.cli, list(args))


def test_version_option():
    result = run("--version")
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_shows_effective_policy():
    result = run("config")
    assert result.exit_code == 0
    assert "execution mode      : local" in result.output
    assert "engine              : ollama" in result.output
    assert "model               : gpt-4o" in result.output
    assert "agent steps limit   : 10" in result.output
    assert "tool execution limit: 4" in result.output
    assert "global tool calls   : 8" in result.output


def test_engine_option_is_honoured_and_showable():
    result = run("--engine", "", "config")
    assert result.exit_code == 0
    assert "engine              : <openjarvis default>" in result.output


def test_config_honours_reasoning_mode():
    result = run("--reasoning-mode", "low", "config")
    assert result.exit_code == 0
    assert "model               : gpt-4o-mini" in result.output
    assert "max tokens          : 512" in result.output


def test_agent_list_contains_native_react():
    result = run("agent", "list")
    assert result.exit_code == 0
    assert "native_react" in result.output


def test_cloud_execution_exits_with_code_two():
    result = run("--execution-mode", "cloud", "ask", "hello")
    assert result.exit_code == 2
    assert "not implemented" in result.output.lower()


def test_ask_prints_content(monkeypatch):
    monkeypatch.setattr(
        cli_module,
        "runtime_ask",
        lambda *args, **kwargs: {"content": "hello there", "tool_results": []},
    )
    result = run("ask", "hi")
    assert result.exit_code == 0
    assert "hello there" in result.output


def test_ask_json_output(monkeypatch):
    monkeypatch.setattr(
        cli_module,
        "runtime_ask",
        lambda *args, **kwargs: {
            "content": "42",
            "tool_results": [{"tool_name": "calculator", "success": True}],
        },
    )
    result = run("ask", "6*7", "--json")
    assert result.exit_code == 0
    assert json.loads(result.output)["content"] == "42"


def test_dev_fix_repairs_project_and_exits_zero(tmp_path, monkeypatch):
    project = tmp_path / "project"
    workspace = Workspace(project)
    for name, content in BROKEN_PROJECT.items():
        workspace.write_text(name, content)
    engine = RepairBotEngine()
    monkeypatch.setattr(cli_module, "engine_for", lambda config: contextlib.nullcontext(engine))

    result = run(
        "dev",
        "fix",
        "--execution-backend",
        "host",
        "--trusted-host-execution",
        "--workspace",
        str(project),
        "--task",
        "Fix the failing calculator test.",
        "--json",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["files_modified"] == ["calculator.py"]
    assert "return a + b" in (project / "calculator.py").read_text(encoding="utf-8")


def test_dev_fix_reports_failure_with_exit_code_one(tmp_path, monkeypatch):
    project = tmp_path / "project"
    workspace = Workspace(project)
    for name, content in BROKEN_PROJECT.items():
        workspace.write_text(name, content)
    monkeypatch.setattr(
        cli_module,
        "engine_for",
        lambda config: contextlib.nullcontext(ScriptedEngine([react_final("done")])),
    )

    result = run(
        "dev",
        "fix",
        "--execution-backend",
        "host",
        "--trusted-host-execution",
        "--workspace",
        str(project),
        "--task",
        "Fix it.",
        "--attempts",
        "1",
    )

    assert result.exit_code == 1
    assert "success     : False" in result.output
