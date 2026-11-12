# SYNVIO Core Agent

A thin, policy-focused layer on top of [OpenJarvis](https://github.com/openjarvis/openjarvis)
(Apache 2.0). SYNVIO does **not** re-implement inference, the agent reasoning
loop, or the tool executor — it selects a mode, enforces hard budgets, and adds
a workspace-bound autonomous developer agent.

```
src/synvio/
  config.py            execution/reasoning modes -> JarvisConfig mapping
  runtime.py           BudgetedTool, run_agent() (NativeReActAgent), engine_for()
  cli.py               `synvio` command line
  autodev/
    workspace.py       path jail + file read/write mutations
    tools.py           dev_list_files / dev_read_file / dev_write_file / dev_run_tests
    runner.py          allowlisted pytest runner, sanitized env, timeout
    agent.py           DeveloperAgent: repair loop, limits, JSONL logs, TaskReport
tests/                 pytest suite (mocked models, real files, real pytest)
examples/bugfix_demo.py  end-to-end demo with a scripted mock model
examples/real_gpt4o_repair.py  opt-in end-to-end repair test with a real model
```

## Install

```powershell
python -m pip install -e .
```

Requires Python >= 3.10 and `openjarvis>=1.0.5` (pinned dependency; verified
against `1.0.5.dev178+g68d42906`).

## CLI

```
synvio config
synvio agent list
synvio ask "hello" --tools calculator
synvio dev fix --workspace . --task "Fix the failing test" --steps 8 --tool-limit 12
```

`synvio ask` and `synvio dev fix` need a real inference engine/API key, and
`ask` therefore costs money. Nothing in the test suite or the demo does that.

## SYNVIO Core local web application

The optional `web/` application provides a localhost-only React/Vite client and
FastAPI API over the existing `DeveloperAgent`. It does not provide a shell
tool, does not send provider keys to the browser, and accepts workspaces only
from the `SYNVIO_WORKSPACES` path-separated allowlist. Workspace access is a
path jail, **not** an OS-level sandbox.

### Windows PowerShell startup

Use Python 3.10+ and Node.js 20+ (LTS recommended). Install the optional Python
dependencies and the frontend dependencies once:

```powershell
cd C:\Users\User\Documents\SYNVIO
$python = 'C:\Users\User\Documents\OpenJarvis\.venv\Scripts\python.exe'
& $python -m pip install -e '.[web,dev]'
cd web
npm install
cd ..
```

In PowerShell window 1, explicitly list the project folders the agent may edit.
Set provider keys in this server process (never in the frontend):

```powershell
$env:SYNVIO_WORKSPACES = 'C:\path\to\your\project'
# The UI's GPT-4o selections are pinned to the OpenJarvis 'cloud' engine and
# OpenAI provider. Populate this variable from your secret manager in this
# backend process only; never put the key in frontend code or task requests.
& $python -m synvio.web_cli
```

The API binds to `127.0.0.1:8000` only. Leave that window running. In PowerShell
window 2, start the Vite client:

```powershell
cd C:\Users\User\Documents\SYNVIO\web
npm run dev
```

Open `http://127.0.0.1:5173`. Task history is stored locally under
`%LOCALAPPDATA%\SYNVIO\Core\tasks.sqlite3` by default (override with
`SYNVIO_DB_PATH`). API keys stay in the backend process environment. The model
picker selects a provider-aware model ID. GPT-4o and GPT-4o mini are pinned to
`engine=cloud`, `provider=openai`; no engine/provider fallback is permitted.
Cloud models are marked unavailable unless their provider key is present in the
backend process environment. Additional models can be declared by the operator
using `SYNVIO_MODEL_CATALOG` (a JSON list with `id`, `provider`, `engine`,
`model`, `reasoning_mode`, and `api_key_env`). Optional local Ollama models must
be explicitly listed in `SYNVIO_LOCAL_MODELS`; the API never assumes Ollama is
installed. This UI accepts software-development tasks, not general conversation
such as “hello”.
Task limits are read from SYNVIO environment configuration and shown in the UI.

Execution logs are persisted from `DeveloperAgent`'s real event callback and
the UI polls that API data; this is event polling, not token streaming. The
current synchronous OpenJarvis loop cannot be interrupted safely. The Cancel
button therefore appears only for a queued task and cancels it before its worker
starts; a running task is explicitly not cancellable. Project tests continue to
run through SYNVIO's existing allowlisted `TestRunner`, never through shell
execution.

### Docker sandbox milestone status

The current Developer Agent still uses SYNVIO's existing host `TestRunner`,
which runs only its fixed pytest/unittest argv with `shell=False`, a sanitized
environment, workspace cwd, and a timeout. This is **not an OS sandbox**.
`SYNVIO_EXECUTION_BACKEND=docker` currently fails closed with HTTP 503; it never
falls back to host execution. Docker is not installed/available in the inspected
development environment, and this project does not install it automatically.

A future Docker backend must be reviewed and tested before enabling it: use a
digest-pinned, explicitly pre-pulled image; disable networking; drop all
capabilities; require non-root/no-new-privileges/read-only rootfs; enforce memory,
CPU, PID and wall-clock limits; never mount the Docker socket or host secrets; and
mount only a carefully staged workspace. Docker is defense in depth, not a
perfect security boundary against hostile code. No sandbox demo is advertised as
executed until Docker is available and the disposable container test passes.

Run backend/API regression tests with:

```powershell
& $python -m pytest tests -q -p no:cacheprovider
```

Build the frontend with `cd web; npm run build`.

### Engine pinning (`--engine`)

The inference engine is pinned (default `ollama`) and passed to the OpenJarvis
SDK as an explicit `engine_key`, which OpenJarvis treats as authoritative: it is
**never** silently substituted. So when the local engine is down, SYNVIO fails
with a clear `no usable inference engine (engine='ollama')` error instead of
quietly falling back to a cloud engine and spending money.

```powershell
synvio config                    # shows the effective engine
synvio --engine nim config       # pin a different engine
synvio --engine "" config        # delegate engine selection to OpenJarvis
```

Execution modes: `local` only. `cloud` and `hybrid` raise
`ExecutionModeNotImplemented` (CLI exit code 2) instead of silently falling
back — they are configuration placeholders in 0.1.0.

Reasoning modes map to model + token budget:

| mode   | model       | max_tokens |
|--------|-------------|-----------|
| low    | gpt-4o-mini | 512       |
| medium | gpt-4o      | 1024      |
| high   | gpt-4o      | 2048      |

## Limits

| Limit                        | Where enforced                       |
|------------------------------|--------------------------------------|
| `agent_steps_limit`          | OpenJarvis `max_turns` (per attempt) |
| `tool_execution_limit`       | nominal per-attempt tool budget (default 4) |
| `total_step_limit`           | task-global steps (all attempts)     |
| `total_tool_limit`           | task-global tool calls (all attempts)|
| `max_repair_attempts`        | `DeveloperAgent.fix()`               |
| `max_test_runs`              | `DevRunTestsTool` (model's own pytest)|
| `test_timeout_seconds`       | `TestRunner` (subprocess timeout)    |

The tool budget is **one shared `ToolBudget` for the whole task**: a repair
attempt never refreshes it, otherwise `max_repair_attempts` would silently
multiply the allowance. `total_step_limit` / `total_tool_limit` default to
`per-attempt limit x max_repair_attempts` and can be pinned explicitly
(`synvio dev fix --global-steps 10 --global-tools 8 --max-test-runs 2`). Each
attempt gets
`min(agent_steps_limit, steps remaining)` turns, and when the global step budget
is gone no further attempt is started.

Two cheap guards keep the budget for the step that actually matters, editing:

- `dev_run_tests` **refuses to run before the first `dev_write_file`** of the
  task. The failure is already in the prompt, so an early run can only burn a
  tool call and a pytest run on reproducing it. The refusal does not count as a
  test run (nothing was executed).
- `dev_write_file` rejects a rewrite with byte-identical content as a no-op, so
  the model cannot believe it changed something and then "verify" it.
- `dev_list_files` hides SYNVIO's own `.synvio/` artifacts.

Every attempt's prompt states the *remaining* real budget
(`budget_brief`), so a repair attempt knows it is not starting fresh.

When a budget is exhausted the tool returns a **failed** `ToolResult`
with a stop instruction; the loop still terminates via `max_turns`. Every run
ends with a `TaskReport` (`success`, `reason`, attempts, steps, tool calls,
denied calls, token usage, per-attempt details, files created/modified, test
outcomes, log path) that is JSON-serialisable.

Reason codes: `tests_passed`, `tool_execution_limit_reached`,
`agent_step_limit_reached`, `global_step_limit_reached`,
`max_repair_attempts_reached`, `agent_error`.

## Test verification is real

After every attempt SYNVIO runs the tests itself (`TestRunner`) and reads the
exit code. The model's claim that tests pass is never trusted: a report is only
`success=True` if a real subprocess returned 0.


## Real model end-to-end test (paid, opt-in)

`examples/real_gpt4o_repair.py` is the only component that can spend money. It
builds a disposable project with a real bug (`slugify()` leaves a trailing
hyphen), points the developer agent at it, and lets a real model fix it.

Pinned, with no fallback of any kind:

| Setting   | Value |
|-----------|-------|
| engine    | `cloud` (OpenJarvis' cloud engine, the only one that serves OpenAI models) |
| provider  | `openai` |
| model     | `gpt-4o` |
| steps     | 10 for the whole task (1 attempt) |
| tool calls| 8 for the whole task (list + reads + write + 2 pytest runs + spare) |
| attempts  | 1 |
| pytest    | the model may run the suite twice; SYNVIO runs it once more itself |
| api key   | `OPENAI_API_KEY` from the environment, never printed |

These numbers were raised after the first paid run: with 5 steps and 4 tool
calls, GPT-4o spent all four calls on `dev_list_files` + two reads + a test run
and had no budget left to edit the file. The pre-edit test gate above makes that
ordering impossible now.

`cloud` is the engine *key* OpenJarvis registers — there is no `openai` engine
key. Because the engine is passed explicitly, OpenJarvis will not substitute
`nim`, `ollama`, or anything else: an unusable engine is a hard error.

OpenJarvis wraps the resolved engine (`InstrumentedEngine` for telemetry,
`GuardrailsEngine` for security) and those wrappers do **not** forward
`is_cloud`, so reading the flag off the SDK's engine object reports `False` even
for the cloud engine. The script therefore unwraps the chain with
`synvio.runtime.identify_engine` and prints the engine that really answered,
e.g. `InstrumentedEngine -> CloudEngine` with `engine_id='cloud', is_cloud=True`.
A local engine makes the paid run fail instead of passing silently.

```powershell
# 1. free dry run: prints the plan, sends nothing, needs no key
cd C:\Users\User\Documents\SYNVIO
& C:\Users\User\Documents\OpenJarvis\.venv\Scripts\python.exe examples\real_gpt4o_repair.py

# 2. paid run: do this in the session where OPENAI_API_KEY is already set
& C:\Users\User\Documents\OpenJarvis\.venv\Scripts\python.exe `
    examples\real_gpt4o_repair.py --confirm-paid --keep "$env:TEMP\synvio-paid-run"
```

Do not paste the key on the command line: the script only reads it from the
environment and prints `set (value hidden)`, so it never lands in shell history
or a log.

The dry run exits 0 when everything is configured and prints the exact plan; it
exits 2 with the specific problem (missing engine, missing key, model the engine
cannot serve) and makes no API call. The paid run prints per-attempt turns and
final answers, every tool call with its arguments and output, the ground-truth
test run SYNVIO performed itself, token usage, the cost from OpenJarvis'
`PRICING` / `estimate_cost`, and the changed files. It exits non-zero if the
tests did not pass or if the model modified the test file.

## Security model (and what it is not)

Enforced:

- **Path jail.** Every file tool resolves its path inside one workspace root;
  absolute paths, drive letters and `..` escapes raise `WorkspaceViolation`.
- **No shell.** `subprocess.run(argv, shell=False)`.
- **Allowlist.** Only `python -m pytest` / `python -m unittest discover` can be
  executed; anything else raises `TestCommandNotAllowed`.
- **No inherited secrets.** The child environment is rebuilt from an allowlist
  (`PATH`, `SYSTEMROOT`, `TEMP`, ...) so `OPENAI_API_KEY`, `AWS_*`, `*_TOKEN`
  never reach the test process.
- **No network tools.** The developer agent's toolset is file + test only.
- **Timeouts and hard limits** on every step, tool call and attempt.
- **No paid calls by default.** The test suite and `bugfix_demo.py` use mocked
  models; only `examples/real_gpt4o_repair.py --confirm-paid` reaches a paid API.

Not claimed:

- This is **not** an OS-level sandbox. `Workspace` is a path jail inside the
  same OS process/user; a malicious test file could still read the host
  filesystem or open sockets. Running untrusted code safely needs a container
  or VM, which is out of scope here and was not installed.
- Model output is only constrained by the tool schema and the parsers in
  OpenJarvis; prompt injection could still try to misuse the allowed tools
  (bounded by the workspace jail and the allowlist).
- The mocked model in tests/demo proves the *plumbing*; real LLM repair quality
  is not measured here.

## Tests

```powershell
python -m pytest tests -q
```

Covered: config/mode mapping, step limits, tool budgets, workspace escapes,
allowlist enforcement, env sanitisation, real pass/fail/timeout detection,
end-to-end repair with a scripted model, CLI exit codes, and OpenJarvis API
compatibility.

## Demo

```powershell
python examples/bugfix_demo.py --keep
```

Creates a temporary project with an intentionally wrong `add()`, has the
(scripted) model read the file, rewrite it, run pytest for real, and stops when
the real tests pass.
