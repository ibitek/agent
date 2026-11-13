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

## Unified website assistant (2026-11-13)

The website now has one composer and durable conversations, without Chat/Task
mode selection. All website labels, auth/storage/runtime errors and default AI
responses are English. User-provided content and legacy stored project data are
not rewritten. Explicit requests for another response language remain supported.
The VS Code extension is paused; its download is no longer advertised in the UI.

`POST /api/assistant` understands requests with one budgeted model loop. It may
reply, inspect up to three actual project snapshots read-only, queue explicit
implementation work, or request existing tests without edits. Inspection has no
write/test tools. A conservative server guard prevents a misclassified question
from authorizing edits; ambiguous requests become a clarification, not an edit.
An explicit confirmation applies only to the saved pending action.
Verification workers never open an implementation model and never publish a
global project update. Their real isolated test results remain visible.

The understanding/inspection loop and subsequent implementation share the
submitted model budget: routing/inspection cost is subtracted before queueing
the worker. Existing daily limits, no automatic paid retries, session auth,
project path restrictions and isolated execution remain enforced. Model intent
quality has only been tested with deterministic fixtures, not a paid live eval.

Conversations/messages are stored in Redis with existing 30-day retention and
a 100-message limit. A request ID plus payload fingerprint and conversation
lock prevents duplicate paid submissions. Incomplete/ambiguous requests are not
automatically retried. Recent execution state is supplied separately from user
messages so the assistant can report actual run status rather than infer it.
Only limited recent history is sent to the model; this is not unlimited memory.

Frontend: animated message entry, genuine pending-request thinking indicator,
hover/press/focus states, reduced-motion support, collapsible sidebar, code-copy
buttons and a side-by-side Preview/Activity workspace. No simulated token stream
or simulated browser mouse activity. Preview capabilities are still limited to
self-contained HTML/CSS/JavaScript; cloud browser and desktop remain unconnected.

Offline browser fixture verified one continuous conversation: free English
greeting, read-only inspection, explicit build request creating one work card,
application preview alongside chat, and history restored after reload. No real
model, remote VM or production project data was used in the fixture.
217 offline Python tests passed before final additional wording/guard cases;
5 UI polling tests passed and production build succeeded. Translation-only test
assertions were updated without weakening host-execution security assertions.


## Confirmation repair and live static-site builder (2026-11-13)

Short approvals including �to dawaj� and �yeah� consume the stored pending action
without another routing model call. A bare approval without a pending action
receives a free clarification. A visible Start this work button submits the same
confirmation path. Conditional or negative messages never match the approval set.

Static website work now has a bounded JSON file-edit loop, using the existing
metered engine, daily request limits and project lock. It cannot invoke shell
commands, install packages or execute generated code on the server. Web text
assets are path/size validated; each successful write persists files and emits
real activity/preview events. Follow-ups inherit the web runtime. Python work
still requires the prepared isolated execution snapshot.

The chat shows recent real file activity. Static reports explicitly say browser
interactions and automated tests were NOT run. Preview bundling is a structural
check, not JavaScript syntax validation or evidence that the app works. Existing
opaque iframe sandbox and restrictive preview CSP are unchanged. There is still
no remote browser video, mouse control or general application runtime.

Validation: full offline Python suite 237 passed, followed by 66 targeted tests
including two additional runtime/confirmation regressions. Five UI polling tests
passed; Vite production build passed. Python imports/tests are separate from
user-project test execution. All model responses were mocked: no paid model call,
remote sandbox allocation or real generated-site browser test was performed.


## Production failure follow-up (2026-11-13)

Observed the authenticated production Activity panel: the reported run stopped
before implementation because the ten-request daily counter charged routing and
execution separately. The run title also contained a model schema placeholder.

A server-only reservation now shares the daily allowance between routing and
its worker. The counter version excludes historical double counting; the daily
cap, per-run model budget and authentication remain enforced. Original user
requirements, rather than the model paraphrase, are passed to implementation.
Explicit retry reuses the failed request without a routing call and skips failed
ancestors with no file snapshot. It never automatically starts a paid retry.

Model replies now follow the latest substantive user message language; short
confirmations retain context. UI remains English. Work answers and actual errors
are displayed directly under the work card. Missing previews explain failures.
The static builder also supports removing project files; prior snapshots remain.

Validation: 242 offline tests passed before the final retry regression; the
updated 70-test cloud/intent/static-builder subset and 5 UI tests passed. Frontend
production build passed. Real production Activity was inspected read-only; no
paid model request or remote sandbox was started by the agent.


## Studio appearance and model choice (2026-11-13)

Added locally bundled Nunito Sans variable font (Fontsource, OFL), rounded
controls, teal accents, subtle background grid and existing reduced-motion-aware
entry/hover animations. Light/dark palette applies to chat, composer, model
picker, preview controls, account and activity panels. Theme follows the OS on
first visit, is saved per browser under synvio-theme-v1, and is applied before
React mounts. No external font request or new analytics is introduced.

Model picker shows distinct gpt-4o-mini, gpt-4o, gpt-4.1-mini and gpt-4.1 choices
with input, cached-input and output token prices. Legacy model IDs still work.
The owner-requested stronger-model opt-in is enabled in Production; the cheap
default, daily request cap and per-message model budget remain unchanged.
Provider account permissions are not guaranteed by the presence of an API key.
New models were tested with mocked responses, not paid production generation.

Rates and Chat Completions compatibility verified from official documentation:
https://developers.openai.com/api/docs/models/gpt-4.1-mini
https://developers.openai.com/api/docs/models/gpt-4.1
Model adapters use existing CloudEngine without changing OpenJarvis.

Validation: 246 offline Python tests, 5 UI polling tests and frontend production
build passed. A loopback fixture verified light/dark appearance and persistence,
model selection, and the side-by-side project preview. The fixture is explicitly
marked DEMO and made no paid API calls. Cloud browser/desktop controls remain
unconnected; the environment panel separates static editing from full execution.


## React / Vite compilation previews (2026-11-13)

The assistant recognizes explicit React/Vite requests and saves a complete source
project through bounded text-file operations. Source-only success is marked
files_only, never tests passed or compiled. Existing static and Python execution
paths retain their protections. Follow-ups inherit the application runtime.

Build preview is an explicit owner action on a terminal task's saved files. It
creates one nonpersistent Vercel VM (1 vCPU, 2 GiB, at most 160 seconds, no ports
and no host credentials). npm installation permits only registry.npmjs.org and
disables lifecycle scripts. Network is denied before running the build. All
project-authored code executes in that VM, never on the SYNVIO host. Context exit
stops and destroys the VM on success or error. No permanent public app URL is
created; bounded text artifacts return to the existing opaque iframe preview.

Build request IDs and a shared lock prevent repeated allocation; successful
artifacts are reused for the same immutable task. Explicit retries after failure
are possible, with a four-build daily test allowance. Infrastructure usage is
separate from the AI budget and is stated before the user clicks. Read-only
status, logs, preview fetches and model selection allocate no VM.

This supports Vite frontend production builds, not backend services, arbitrary
frameworks, browser video, or computer control. Native/binary build assets,
imported chunks and oversized bundles may not render in the restricted preview.
An actual compiler pass is distinguished from browser/interaction testing.

Validation: 254 full offline tests passed before the final source-only regression;
56 targeted cloud/build/source tests plus 5 UI polling tests passed afterward.
Frontend build passed. A loopback browser fixture verified the Build preview
button, real UI status/log flow and resulting iframe using explicitly labeled
DEMO data. All Sandbox SDK calls were mocked. No real Vercel VM or model call
was made for this change, so production infrastructure permissions and end-to-end
React compilation still require a real owner-triggered run.

SDK contract checked against installed vercel Python package and official docs:
https://vercel.com/docs/sandbox/python-sdk-reference


## Builder layout correction (2026-11-13)

Replaced the permanent three-column layout with the requested builder composition:
compact conversation at left, large application canvas at right, history in a
drawer, Preview / Code & files / Activity toolbar, address/reload controls, and
expand-preview action. Saved conversations open their application preview
automatically. Source changes use existing authenticated read-only file APIs.

Build diagnostics are collapsed by default so npm output does not consume the
application canvas. Theme fixes now cover success cards, file headers, cost
metrics and final-answer cards, including legacy selectors that previously
left dark backgrounds under dark text in light mode. Known step/tool/attempt
counts are shown even when a report does not include maximum limits.

Validation: frontend build and UI polling tests; loopback browser fixture checked
the two-column composition, history drawer, source-code view, light-mode activity
contrast, dark-mode preview and expand/restore. No model or cloud build was
started during interface verification. Existing generated application files and
server execution/security policies were not changed.
