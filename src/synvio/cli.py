"""SYNVIO command line interface.

Commands:
    synvio config                 show the effective SYNVIO policy
    synvio agent list             list OpenJarvis agents
    synvio ask "..."              ask the agent (real inference engine)
    synvio dev fix --workspace .  run the autonomous developer agent

Cloud/hybrid execution modes are configuration placeholders in 0.1.0 and exit
with code 2 instead of silently falling back to local execution.
"""

from __future__ import annotations

import json
import sys
from typing import List, Optional

import click

from synvio import __version__
from synvio.autodev.agent import DeveloperAgent
from synvio.autodev.workspace import Workspace
from synvio.config import (
    ExecutionMode,
    ExecutionModeNotImplemented,
    ReasoningMode,
    SYNVIOConfig,
)
from synvio.runtime import ask as runtime_ask
from synvio.runtime import engine_for

EXIT_NOT_IMPLEMENTED = 2


def _config_from_context(ctx: click.Context) -> SYNVIOConfig:
    return ctx.obj["config"]


def _fail(ctx: click.Context, message: str, code: int = 1) -> None:
    click.echo(f"error: {message}", err=True)
    ctx.exit(code)


@click.group()
@click.version_option(__version__, prog_name="synvio")
@click.option(
    "--execution-mode",
    "execution_mode",
    type=click.Choice([mode.value for mode in ExecutionMode]),
    default=ExecutionMode.LOCAL.value,
    show_default=True,
    help="Execution mode (only 'local' is implemented).",
)
@click.option(
    "--reasoning-mode",
    "reasoning_mode",
    type=click.Choice([mode.value for mode in ReasoningMode]),
    default=ReasoningMode.MEDIUM.value,
    show_default=True,
    help="Reasoning budget; selects the model.",
)
@click.option(
    "--engine",
    "engine",
    default="ollama",
    show_default=True,
    help="Inference engine to pin ('' lets OpenJarvis choose).",
)
@click.pass_context
def cli(
    ctx: click.Context, execution_mode: str, reasoning_mode: str, engine: str
) -> None:
    """SYNVIO Core Agent (built on OpenJarvis)."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = SYNVIOConfig(
        execution_mode=execution_mode,
        reasoning_mode=reasoning_mode,
        engine=engine,
    )


@cli.command("config")
@click.pass_context
def cmd_config(ctx: click.Context) -> None:
    """Show the effective SYNVIO configuration."""
    config = _config_from_context(ctx)
    click.echo(f"synvio version      : {__version__}")
    click.echo(f"execution mode      : {config.execution_mode.value}")
    click.echo(f"reasoning mode      : {config.reasoning_mode.value}")
    click.echo(f"engine              : {config.engine or '<openjarvis default>'}")
    click.echo(f"model               : {config.model}")
    click.echo(f"temperature         : {config.temperature}")
    click.echo(f"max tokens          : {config.max_tokens}")
    click.echo(f"agent steps limit   : {config.agent_steps_limit}")
    click.echo(f"tool execution limit: {config.tool_execution_limit}")
    click.echo(f"repair attempts     : {config.max_repair_attempts}")
    click.echo(f"global steps        : {config.global_step_limit}")
    click.echo(f"global tool calls   : {config.global_tool_limit}")
    click.echo(f"pytest runs (model) : {config.max_test_runs or '<tool budget>'}")
    click.echo(f"test timeout (s)    : {config.test_timeout_seconds}")


@cli.group("agent")
def agent_group() -> None:
    """Inspect OpenJarvis agents."""


@agent_group.command("list")
def cmd_agent_list() -> None:
    """List registered OpenJarvis agents."""
    import openjarvis.agents  # noqa: F401  (triggers registrations)
    from openjarvis.core.registry import AgentRegistry

    for name in sorted(AgentRegistry.keys()):
        click.echo(name)



@cli.command("ask")
@click.argument("query")
@click.option("--agent", "agent_name", default="native_react", show_default=True)
@click.option("--tools", "tools", multiple=True, help="Tool names to enable.")
@click.option("--json", "as_json", is_flag=True, help="Print the raw result dict.")
@click.pass_context
def cmd_ask(
    ctx: click.Context,
    query: str,
    agent_name: str,
    tools: List[str],
    as_json: bool,
) -> None:
    """Ask the agent a single question."""
    config = _config_from_context(ctx)
    try:
        config.require_supported_execution()
    except ExecutionModeNotImplemented as exc:
        _fail(ctx, str(exc), EXIT_NOT_IMPLEMENTED)

    try:
        result = runtime_ask(query, config, agent=agent_name, tools=list(tools))
    except ExecutionModeNotImplemented as exc:  # pragma: no cover - defensive
        _fail(ctx, str(exc), EXIT_NOT_IMPLEMENTED)
    except Exception as exc:
        _fail(ctx, f"{type(exc).__name__}: {exc}")

    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return
    click.echo(result.get("content", ""))
    if result.get("tool_results"):
        click.echo("\nTool calls:", err=True)
        for item in result["tool_results"]:
            state = "ok" if item.get("success") else "failed"
            click.echo(f"  - {item.get('tool_name')} [{state}]", err=True)


@cli.group("dev")
def dev_group() -> None:
    """Autonomous developer agent (workspace-bound)."""


@dev_group.command("fix")
@click.option(
    "--workspace",
    "workspace_path",
    required=True,
    type=click.Path(file_okay=False, path_type=str),
    help="Project directory the agent is allowed to modify.",
)
@click.option("--task", "task", required=True, help="What the agent must fix.")
@click.option("--steps", "steps", type=int, default=None, help="Steps per attempt.")
@click.option(
    "--tool-limit", "tool_limit", type=int, default=None, help="Tool calls per attempt."
)
@click.option("--attempts", "attempts", type=int, default=None, help="Repair attempts.")
@click.option(
    "--global-steps",
    "global_steps",
    type=int,
    default=None,
    help="Steps for the whole task, all attempts included (default: steps x attempts).",
)
@click.option(
    "--global-tools",
    "global_tools",
    type=int,
    default=None,
    help="Tool calls for the whole task (default: tool-limit x attempts).",
)
@click.option(
    "--max-test-runs",
    "--test-runs",
    "max_test_runs",
    type=int,
    default=None,
    help="How many times the model may run the project's tests (default: tool budget).",
)
@click.option("--model", "model", default=None, help="Override the model id.")
@click.option(
    "--execution-backend",
    type=click.Choice(["docker", "host"]),
    default="docker",
    show_default=True,
    help="Test backend. Host execution is trusted development mode only.",
)
@click.option("--docker-image", default=None, help="Digest-pinned local Docker test image.")
@click.option("--trusted-host-execution", is_flag=True, help="Acknowledge that host tests are not sandboxed.")
@click.option("--json", "as_json", is_flag=True, help="Print the full task report.")
@click.pass_context
def cmd_dev_fix(
    ctx: click.Context,
    workspace_path: str,
    task: str,
    steps: Optional[int],
    tool_limit: Optional[int],
    attempts: Optional[int],
    global_steps: Optional[int],
    global_tools: Optional[int],
    max_test_runs: Optional[int],
    model: Optional[str],
    execution_backend: str,
    docker_image: Optional[str],
    trusted_host_execution: bool,
    as_json: bool,
) -> None:
    """Run the developer agent against a project workspace."""
    base = _config_from_context(ctx)
    overrides = {
        "agent_steps_limit": steps,
        "tool_execution_limit": tool_limit,
        "max_repair_attempts": attempts,
        "total_step_limit": global_steps,
        "total_tool_limit": global_tools,
        "max_test_runs": max_test_runs,
    }
    try:
        config = base.replace(
            **{key: value for key, value in overrides.items() if value is not None}
        )
    except ValueError as exc:
        _fail(ctx, str(exc), EXIT_NOT_IMPLEMENTED)

    try:
        config.require_supported_execution()
    except ExecutionModeNotImplemented as exc:
        _fail(ctx, str(exc), EXIT_NOT_IMPLEMENTED)

    # The Docker sandbox was not completed. Fail closed instead of ever
    # executing an explicitly Docker-scoped request on the Windows host.
    if execution_backend == "docker":
        _fail(ctx, "Docker sandbox execution is not implemented yet; no host fallback", EXIT_NOT_IMPLEMENTED)
    if not trusted_host_execution:
        _fail(ctx, "Host tests are not sandboxed; pass --trusted-host-execution only for trusted development workspaces")
    if docker_image is not None:
        _fail(ctx, "--docker-image is only applicable to the unavailable Docker backend", EXIT_NOT_IMPLEMENTED)

    workspace = Workspace(workspace_path)
    try:
        with engine_for(config) as engine:
            agent = DeveloperAgent(
                engine=engine, workspace=workspace, config=config, model=model,
            )
            report = agent.fix(task)
    except Exception as exc:
        _fail(ctx, f"{type(exc).__name__}: {exc}")
        return

    if as_json:
        click.echo(report.to_json())
    else:
        click.echo(f"success     : {report.success}")
        click.echo(f"reason      : {report.reason}")
        click.echo(f"attempts    : {report.attempts}")
        click.echo(
            f"steps used  : {report.steps_used} "
            f"(limit {report.total_step_limit} total, {report.step_limit}/attempt)"
        )
        click.echo(
            f"tool calls  : {report.tool_calls} "
            f"(limit {report.total_tool_limit} total, {report.tool_limit}/attempt)"
        )
        if report.tool_calls_denied:
            click.echo(f"denied      : {report.tool_calls_denied} tool calls")
        if report.tokens.get("total_tokens"):
            click.echo(
                f"tokens      : {report.tokens.get('prompt_tokens', 0)} prompt + "
                f"{report.tokens.get('completion_tokens', 0)} completion"
            )
        click.echo(f"modified    : {', '.join(report.files_modified) or '-'}")
        click.echo(f"created     : {', '.join(report.files_created) or '-'}")
        click.echo(f"log         : {report.log_path}")
    if not report.success:
        ctx.exit(1)


def main(argv: Optional[List[str]] = None) -> None:
    """Console-script entry point (``synvio``)."""
    cli.main(args=argv, prog_name="synvio", standalone_mode=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
