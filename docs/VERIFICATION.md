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
