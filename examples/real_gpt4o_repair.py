"""Real end-to-end repair test driven by GPT-4o through OpenJarvis.

The rest of the SYNVIO test suite is offline and uses scripted engines. This
script is the opposite: it builds a small disposable project with a deliberate
bug, hands it to the developer agent, and lets a *real* model fix it.

Safety properties (all enforced here, not just documented):

* the engine is pinned to ``cloud`` and the model to ``gpt-4o``; OpenJarvis
  treats an explicit engine as authoritative, so a failure can never fall back
  to NVIDIA NIM, a local model, or any other provider;
* nothing runs without ``--confirm-paid``: the default mode is a dry run that
  only prints the plan and touches no network;
* the API key is read from the environment and never printed;
* the workspace is a fresh temporary directory and a path jail, not an OS
  sandbox (tests still run as the current user);
* the agent gets 10 ReAct steps and 8 tool calls for the *whole task* (one
  attempt, no retry budget), and may execute pytest at most twice: once to
  verify the fix and once to re-verify after a further edit; SYNVIO runs the
  suite once more itself as ground truth;
* the engines OpenJarvis resolves are unwrapped (``InstrumentedEngine`` and
  ``GuardrailsEngine`` do not forward ``is_cloud``), so the reported engine is
  the one that really answered;
* the test file is fingerprinted before the run: if the model rewrites the
  tests instead of the code, the run is reported as tampered with.

Usage (dry run, free):

    python examples/real_gpt4o_repair.py

Usage (real API calls, costs money), in the PowerShell session that already has
OPENAI_API_KEY set:

    python examples/real_gpt4o_repair.py --confirm-paid --keep "$env:TEMP\synvio-paid-run"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:  # runnable without `pip install -e .`
    sys.path.insert(0, str(ROOT / "src"))

from synvio.autodev.agent import REASON_TESTS_PASSED, DeveloperAgent  # noqa: E402
from synvio.autodev.workspace import Workspace  # noqa: E402
from synvio.config import SYNVIOConfig  # noqa: E402
from synvio.runtime import EngineIdentity, engine_for, identify_engine  # noqa: E402

# -- pinned configuration (no fallback, no provider substitution) ----------

ENGINE = "cloud"  # OpenJarvis' cloud engine is the one that serves OpenAI models
MODEL = "gpt-4o"
PROVIDER = "openai"
API_KEY_ENV = "OPENAI_API_KEY"

STEPS_PER_ATTEMPT = 10
TOOL_CALLS_PER_ATTEMPT = 8  # list, 2 reads, write, 2 pytest runs, 2 spare
MAX_ATTEMPTS = 1
MAX_TEST_RUNS = 2  # verify the fix, and re-verify after one more edit
TOTAL_STEPS = STEPS_PER_ATTEMPT * MAX_ATTEMPTS
TOTAL_TOOL_CALLS = TOOL_CALLS_PER_ATTEMPT * MAX_ATTEMPTS

TASK = (
    "The test suite in this project fails. Read the project, work out why "
    "slugify() returns the wrong value, fix slugify.py and run the tests. "
    "Do not modify or disable the tests."
)

TEST_TARGET = "test_slugify.py"

# -- disposable project ----------------------------------------------------

BUGGY_SLUGIFY = '''"""Turn a title into a URL slug."""

import re


def slugify(text: str) -> str:
    """Lowercase the words of *text* and join them with single hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower())
'''

SLUGIFY_TESTS = '''from slugify import slugify


def test_basic():
    assert slugify("Hello World") == "hello-world"


def test_surrounding_whitespace():
    assert slugify("  Hello,   World!  ") == "hello-world"


def test_trailing_punctuation():
    assert slugify("OpenJarvis!!!") == "openjarvis"
'''

PROJECT_FILES: Dict[str, str] = {
    "slugify.py": BUGGY_SLUGIFY,
    TEST_TARGET: SLUGIFY_TESTS,
    "pytest.ini": "[pytest]\ntestpaths = .\n",
}


def build_project(root: Path) -> None:
    """Write the broken project into a fresh workspace."""
    workspace = Workspace(root)
    for name, content in PROJECT_FILES.items():
        workspace.write_text(name, content)
    workspace.clear_mutations()


def fingerprint(root: Path) -> str:
    return (root / TEST_TARGET).read_text(encoding="utf-8")


def build_config(model: str = MODEL) -> SYNVIOConfig:
    """Explicit engine + model + budgets. Nothing is left to chance."""
    return SYNVIOConfig(
        execution_mode="local",
        reasoning_mode="medium",
        engine=ENGINE,
        provider=PROVIDER,
        model_override=model,
        agent_steps_limit=STEPS_PER_ATTEMPT,
        tool_execution_limit=TOOL_CALLS_PER_ATTEMPT,
        max_repair_attempts=MAX_ATTEMPTS,
        total_step_limit=TOTAL_STEPS,
        total_tool_limit=TOTAL_TOOL_CALLS,
        max_test_runs=MAX_TEST_RUNS,
        test_timeout_seconds=60.0,
    )


# -- preflight -------------------------------------------------------------


def pricing(model: str = MODEL) -> Optional[Dict[str, Any]]:
    """Return OpenJarvis' own price table entry for the model, if available."""
    try:
        from openjarvis.engine.cloud import PRICING, estimate_cost
    except Exception:  # pragma: no cover - optional engine package
        return None
    if model not in PRICING:
        return None
    prompt_price, completion_price = PRICING[model]
    return {
        "prompt_usd_per_mtok": prompt_price,
        "completion_usd_per_mtok": completion_price,
        "estimate_cost": estimate_cost,
    }


def preflight(config: SYNVIOConfig, model: str = MODEL) -> List[str]:
    """Collect every reason the paid run cannot start. No network calls."""
    problems: List[str] = []

    import openjarvis.engine  # noqa: F401  (triggers engine registrations)
    from openjarvis.core.registry import EngineRegistry

    if ENGINE not in EngineRegistry.keys():
        problems.append(
            f"engine {ENGINE!r} is not registered; available: "
            f"{', '.join(sorted(EngineRegistry.keys()))}"
        )
    if config.engine != ENGINE:
        problems.append(f"engine is pinned to {config.engine!r}, expected {ENGINE!r}")
    if config.model != model:
        problems.append(f"model resolved to {config.model!r}, expected {model!r}")
    if not os.environ.get(API_KEY_ENV):
        problems.append(
            f"{API_KEY_ENV} is not set. Set it in the environment (do not paste "
            "the key into any file in this repository) and re-run."
        )

    try:
        from openjarvis.engine.cloud import CloudEngine

        engine = CloudEngine()
        try:
            serves = engine.can_serve(MODEL)
        finally:
            engine.close()
        if not serves:
            problems.append(
                f"the {ENGINE!r} engine cannot serve {MODEL!r} "
                f"({API_KEY_ENV} missing or the openai SDK is unavailable)"
            )
    except Exception as exc:  # pragma: no cover - depends on the host
        problems.append(f"could not construct the {ENGINE!r} engine: {exc}")

    return problems


def print_plan(config: SYNVIOConfig, workspace: Path, keep: Optional[Path]) -> None:
    table = pricing(config.model)
    key_state = "set (value hidden)" if os.environ.get(API_KEY_ENV) else "MISSING"
    print("SYNVIO real-model repair test - run plan")
    print(f"  provider            : {config.provider}")
    print(f"  engine (pinned)     : {config.engine}")
    print(f"  model (pinned)      : {config.model}")
    print(f"  reasoning mode      : {config.reasoning_mode.value}")
    print(f"  temperature         : {config.temperature}")
    print(f"  max tokens / step   : {config.max_tokens}")
    print(f"  steps per attempt   : {config.agent_steps_limit}")
    print(f"  tool calls/attempt  : {config.tool_execution_limit}")
    print(f"  repair attempts     : {config.max_repair_attempts}")
    print(f"  global steps        : {config.global_step_limit}")
    print(f"  global tool calls   : {config.global_tool_limit}")
    print(f"  pytest runs (model) : {config.max_test_runs}")
    print(f"  test timeout        : {config.test_timeout_seconds:.0f}s")
    print(f"  workspace           : {workspace}")
    print(f"  keep workspace      : {keep or '<deleted after the run>'}")
    print(f"  project files       : {', '.join(sorted(PROJECT_FILES))}")
    print(f"  test target         : {TEST_TARGET}")
    print(f"  api key             : {API_KEY_ENV} {key_state}")
    if table:
        worst_prompt = TOTAL_STEPS * 2500
        worst_completion = TOTAL_STEPS * config.max_tokens
        worst = (
            worst_prompt * table["prompt_usd_per_mtok"] / 1_000_000
            + worst_completion * table["completion_usd_per_mtok"] / 1_000_000
        )
        print(
            "  pricing             : "
            f"${table['prompt_usd_per_mtok']:.2f}/Mtok in, "
            f"${table['completion_usd_per_mtok']:.2f}/Mtok out (OpenJarvis PRICING)"
        )
        print(
            "  worst-case estimate : "
            f"~${worst:.2f} for {TOTAL_STEPS} steps at <=2500 prompt tokens and "
            f"<={config.max_tokens} completion tokens each"
        )
    else:  # pragma: no cover - defensive
        print("  pricing             : unavailable for this model")


# -- reporting -------------------------------------------------------------


def print_tool_calls(events: List[Dict[str, Any]]) -> None:
    calls = [event for event in events if event.get("event") == "tool_call"]
    print(f"\nTool calls ({len(calls)}):")
    for index, call in enumerate(calls, start=1):
        state = "allowed" if call.get("allowed") else "denied"
        outcome = "ok" if call.get("success") else "failed"
        print(
            f"  {index}. {call.get('tool')} [{state}/{outcome}] "
            f"{call.get('used')}/{call.get('limit')}"
        )
        arguments = json.dumps(call.get("arguments", {}), default=str)
        print(f"     args   : {arguments[:300]}")
        output = str(call.get("output", "")).replace("\n", "\n              ")
        print(f"     output : {output[:400]}")


def print_report(
    report, root: Path, before: str, identity: EngineIdentity
) -> None:
    print("\n=== result ===")
    print(f"success      : {report.success}")
    print(f"reason       : {report.reason}")
    print(f"attempts     : {report.attempts}")
    print(f"steps used   : {report.steps_used} (global limit {report.total_step_limit})")
    print(
        f"tool calls   : {report.tool_calls} (global limit {report.total_tool_limit}, "
        f"denied {report.tool_calls_denied})"
    )
    print(f"duration     : {report.duration_seconds:.1f}s")
    print(f"model        : {report.model}")
    proof = "no local fallback" if identity.is_cloud else "LOCAL FALLBACK - invalid run"
    print(f"engine used  : {identity.name}")
    print(
        f"               pinned {ENGINE}, engine_id={identity.engine_id!r}, "
        f"is_cloud={identity.is_cloud} - {proof}"
    )
    if report.error:
        print(f"error        : {report.error}")

    print("\nPer attempt:")
    for detail in report.attempt_details:
        print(
            f"  attempt {detail['attempt']}: {detail['turns']}/"
            f"{detail['turns_allowed']} steps, {detail['tool_calls']} tool calls, "
            f"step_limit_hit={detail['step_limit_hit']}"
        )
        answer = (detail.get("final_answer") or "").strip().replace("\n", " ")
        if answer:
            print(f"    final answer: {answer[:400]}")

    print_tool_calls(report.events)

    if report.tests:
        last = report.tests[-1]
        print("\nGround-truth test run by SYNVIO (not the model):")
        print(f"  command  : {' '.join(last['command'])}")
        print(f"  passed   : {last['passed']} (exit {last['returncode']})")
        tail = (last.get("stdout_tail") or "").strip().splitlines()
        for line in tail[-4:]:
            print(f"  | {line}")

    tokens = report.tokens or {}
    print("\nUsage:")
    print(f"  prompt tokens     : {tokens.get('prompt_tokens', 0)}")
    print(f"  completion tokens : {tokens.get('completion_tokens', 0)}")
    print(f"  total tokens      : {tokens.get('total_tokens', 0)}")
    table = pricing(report.model or MODEL)
    if table and tokens.get("total_tokens"):
        cost = table["estimate_cost"](
            report.model or MODEL,
            tokens.get("prompt_tokens", 0),
            tokens.get("completion_tokens", 0),
        )
        print(f"  estimated cost    : ${cost:.4f} (OpenJarvis estimate_cost)")
    else:  # pragma: no cover - defensive
        print("  estimated cost    : unavailable")

    print("\nChanged files:")
    print(f"  modified : {', '.join(report.files_modified) or '-'}")
    print(f"  created  : {', '.join(report.files_created) or '-'}")
    if fingerprint(root) != before:
        print(
            f"  WARNING  : {TEST_TARGET} was modified - the tests are no longer "
            "a trustworthy oracle"
        )

    print("\nArtifacts:")
    print(f"  workspace : {report.workspace_root}")
    print(f"  log       : {report.log_path}")
    print(f"  report    : {root / '.synvio' / 'report.json'}")


# -- main ------------------------------------------------------------------


def run(config: SYNVIOConfig, root: Path):
    """Run the developer agent with the pinned engine/model.

    Returns the report plus the identity of the engine OpenJarvis really
    resolved (wrappers stripped), so the output proves that no fallback engine
    was substituted.
    """
    workspace = Workspace(root)
    with engine_for(config) as engine:
        identity = identify_engine(engine)
        agent = DeveloperAgent(
            engine=engine, workspace=workspace, config=config, model=config.model
        )
        return agent.fix(TASK, test_target=TEST_TARGET), identity


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Real GPT-4o repair test (dry run unless --confirm-paid)."
    )
    parser.add_argument(
        "--confirm-paid",
        action="store_true",
        help="Actually call the OpenAI API. Without this flag nothing is sent.",
    )
    parser.add_argument("--model", default=MODEL, help=f"Model id (default {MODEL}).")
    parser.add_argument(
        "--keep",
        default=None,
        help="Copy the disposable workspace here before exiting (for inspection).",
    )
    parser.add_argument("--json", action="store_true", help="Print the raw report.")
    args = parser.parse_args(argv)

    config = build_config(args.model)
    keep = Path(args.keep).resolve() if args.keep else None

    temp_dir = Path(tempfile.mkdtemp(prefix="synvio-real-"))
    root = temp_dir / "project"
    build_project(root)
    before = fingerprint(root)

    print_plan(config, root, keep)

    problems = preflight(config, args.model)
    if problems:
        print("\nPreflight failed - no API call was made:")
        for problem in problems:
            print(f"  - {problem}")
        _copy(temp_dir, keep)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return 2

    if not args.confirm_paid:
        print("\nDry run only: re-run with --confirm-paid to spend real tokens.")
        _copy(temp_dir, keep)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return 0

    print("\nRunning the agent against the real model...")
    try:
        report, identity = run(config, root)
    except Exception as exc:
        print(f"\nRun aborted: {type(exc).__name__}: {exc}")
        _copy(temp_dir, keep)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return 1

    (root / ".synvio" / "report.json").write_text(report.to_json(), encoding="utf-8")

    if args.json:
        print(report.to_json())
    else:
        print_report(report, root, before, identity)

    tampered = fingerprint(root) != before
    ok = (
        report.success
        and not tampered
        and report.reason == REASON_TESTS_PASSED
        # The unwrapped engine must be the pinned cloud engine: anything else
        # means OpenJarvis substituted a provider, which invalidates the run.
        and identity.is_cloud
        and identity.engine_id == ENGINE
    )

    _copy(temp_dir, keep)
    shutil.rmtree(temp_dir, ignore_errors=True)
    return 0 if ok else 1


def _copy(temp_dir: Path, keep: Optional[Path]) -> None:
    if not keep:
        return
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(temp_dir, keep, dirs_exist_ok=True)
    print(f"\nWorkspace copied to {keep}")


if __name__ == "__main__":
    raise SystemExit(main())
