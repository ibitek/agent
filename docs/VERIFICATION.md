# Verification — 2026-11-12

- Fresh Python 3.12.16 environment; OpenJarvis pinned to commit `68d4290663984508db94a6a59ade45c2b17859ed`.
- Full offline Python suite: **163 passed in 36.25 seconds**. Models, Redis REST and remote sandbox handles are simulated. Existing core tests execute actual pytest in disposable projects.
- Node polling tests: **5 passed** (terminal events, stale history, selection races, pagination, temporary network failures).
- Production frontend build: **PASS**, Vite 6.4.6, 1581 modules.
- npm dependency audit after the targeted Vite update: **0 vulnerabilities reported**.
- Browser QA on a separate local preview: login succeeds, public API is protected, chat is the default, and `siema` produces a local response showing **$0 / no tools**.
- All source modules have valid Python syntax, including the dormant legacy Docker file; Docker was not enabled.
- Staged source checked for common credential patterns and accidentally included runtime files. Actual environment values, data stores, local task history, `.venv` and `node_modules` are excluded.
- No new paid inference calls or real Vercel Sandbox allocations were made while preparing this repository.

## Verification boundary

This is **not a successful production deployment report**. Vercel build/routing, real Redis connectivity, OIDC, image preparation and remote test execution must still be verified against the connected Vercel project after import and configuration. The application keeps task execution unavailable until its durable storage and isolated test image are configured; it never substitutes host execution.

The local application in `C:\Users\User\Documents\SYNVIO` was not replaced by this cloud adapter, and no OpenJarvis source checkout was modified.

## Model cost meter (2026-11-12)

Cloud chat/task requests accept `model_budget_usd` (0 < value <= 1 USD;
default chat 0.01, task 0.10). Browser lets the owner choose the limit.
This controls one request/task, not an account wallet or a daily USD cap.
Every sequential inference gets a conservative no-cache reservation before
network access. UTF-8 payload bytes plus message/framing allowance bound text
input for the two approved BPE models; output uses the explicit generation cap.
It can stop early. It is a guard using the pinned rate card, not a provider-side
spend guarantee or a maximum infrastructure bill. Pricing changes need review.

The SYNVIO adapter observes raw OpenAI usage before the pinned SDK drops cache
details. It disables SDK automatic retries and requests the standard service tier.
No OpenJarvis files are modified. Unknown cache usage remains null, not zero.
Costs use decimal arithmetic and a versioned standard text rate card from:
- https://developers.openai.com/api/docs/models/gpt-4o-mini
- https://developers.openai.com/api/docs/models/gpt-4o

Reservations and per-call observations persist before/after generation. Timeout,
missing/invalid usage or another ambiguous failure retain an unresolved reservation
and prevent further calls in that run. They do NOT debit a user balance.
A persisted task cost record survives an agent exception, even when its aggregate
legacy token report is incomplete. `/api/costs` exposes recent owner-only records,
including chat failures, without prompt text or credentials. Retention is 30 days;
this is operational telemetry, not a financial ledger suitable for customer billing.
Sandbox, hosting, storage, VAT, currency conversion and retail margin are excluded.
Completed/failed tasks are not automatically resumed when budget is exhausted.

Validation: mocked SDK response with cached token details; budget refusal before
provider; persistence before provider; timeout/no retry; malformed usage; storage
failure; authenticated cost history; existing authentication and cloud API tests.
No paid inference or real sandbox allocation is part of this verification.

## Follow-ups and application preview (2026-11-13)

Task API accepts `parent_id`. The parent must be terminal, have saved files,
and be the latest committed version in its thread. Active, expired/missing and
stale versions fail before inference. Recheck the thread head after acquiring
the existing project lock. Workers load that immutable parent file snapshot,
not the unrelated latest global project. Context includes at most three previous
requests/answers, bounded to 1,200/800 characters and marked untrusted history.
Each follow-up retains a separate inference budget and cost record.

Sidebar groups execution stages into a single thread; the stage navigator opens
earlier files, diffs, previews and reports. The follow-up composer is shown on
the latest saved stage, with no active stage in that thread. There is no branch
merge, unlimited context, resumable workflow or multi-project concurrency yet.

`GET /api/tasks/{id}/preview` returns JSON, never generated HTML on the SYNVIO
origin. Frontend renders `srcDoc` in an iframe with `sandbox=allow-scripts` and
no `allow-same-origin`, forms, popups or top-navigation permission. CSP limits
script/style/image sources and blocks fetch/connect, frames, objects and forms.
No production credentials or application cookies are provided to the iframe.
This isolates preview code from the host app; it is not a VM or a comprehensive
network firewall for every browser navigation behavior.

Standalone HTML with local CSS and classic JavaScript is supported. External
script/style dependencies, missing assets, JSX and module imports return a clear
unavailable reason; Python/Node backends and unbuilt React require a remote
runtime. Preview is not a test-pass signal. Successful file writes publish
preview snapshots/events; the open viewer refreshes after those real writes.

Browser smoke verification used an isolated loopback fixture with a fake worker,
no cloud data or model calls: preview opened, button executed inside the iframe,
follow-up submission preserved parent/thread IDs, changed preview appeared,
sidebar grouped the two stages, older stage disabled new follow-up, and the
Environment dialog displayed disconnected computer/browser states.
186 offline Python tests passed, plus the production frontend build.

## Planned remote interaction modes (not connected)

The user wants both a cloud browser and local computer control. Neither is
implemented by the Environment dialog. Cloud-browser delivery needs a real
isolated browser session, bounded lifetime, screenshots/video stream and events
for actual pointer/keyboard actions. Local mode needs a separately installed,
signed companion with one-time pairing, visible active-session indication,
scoped permissions, immediate local Stop, revocation and disconnect handling.
A webpage alone must never claim control of the user's computer. No inbound
public desktop port or server API key stored inside the desktop/browser runtime.
Production still needs model credentials and durable task storage before paid
agent tasks can be verified end-to-end. No paid model/sandbox test was run.

## VS Code companion 0.1 (2026-11-13)

Packaged with @vscode/vsce 4.1.1 as a real VSIX, with no runtime npm dependencies.
Source: extensions/vscode. Hosted download: /downloads/synvio-workspace-0.1.0.vsix.
Artifact bytes were compared with the checked source and ZIP integrity verified.
The extension has not been installed into the user's VS Code or Marketplace.
No real model task has been used to validate it in a VS Code extension host.

Authentication uses the app's existing Supabase account. Password entry is via
a native password prompt and goes directly to Supabase HTTPS; only refresh/access
tokens are stored in SecretStorage. The public config endpoint rejects non-
publishable keys; it never exposes model or service_role credentials. Bearer
auth is accepted only on editor routes and still validates the owner allowlist
against Supabase. Browser Origin checks remain in place when Origin is present.

A project must be trusted and explicitly selected/approved for sending source
content to SYNVIO and its model provider. Local actions: list, read, proposed
write, final. No arbitrary commands, shell, terminal, web or desktop control.
Known secret files, hidden paths, traversal, drive paths, symlinks and binary or
oversized files are blocked. Writes require a VS Code diff plus per-file approval;
existing dirty buffers and file changes during review are rejected. An accepted
change is saved only if its buffer still matches the approved content. This is
user-authorized host editing, not an OS sandbox or full secret scanner.

The model loop uses server-held model credentials, 8 sequential turns, and one
shared model budget. Cost telemetry is persisted per turn and surfaced through
/api/costs. Duplicate step numbers replay the saved action rather than invoking
the provider again. Unknown costs block further turns. Stop aborts local requests
and writes a persistent server stop flag; subsequent local actions check abort
state before applying an edit. It cannot guarantee that an already-started
provider call stops billing. Conversation context survives follow-ups in the
current panel only; website task history is not yet synchronized with it.

Verification: 197 Python offline tests before final additive history changes,
7 Node extension tests (including write decline, conflict and Stop), 5 existing
UI tests, syntax checks and frontend build. Test model/network calls are mocked.
No paid inference, host project changes or remote test VM allocations were run.

Upstash was provisioned with explicit user approval of integration terms and
only the Free plan, no eviction/read replicas, iad1, connected to Production
only. Secret values were never revealed or copied to the local workspace.
Production metadata confirms model and Redis variable presence; authenticated
UI reload confirmed the missing-Redis error was gone. Task environment setup is
a separate owner-triggered action; no real sandbox bootstrap has been invoked.
