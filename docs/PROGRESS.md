HISTORICAL SOURCE DOCUMENT: copied from the earlier local Northstar project. Its former machine settings, live-account setup and evidence do not apply to a fresh clone of this release. Use README.md and docs/VALIDATION_2026-11-08.md for current setup, measured results and limitations. No old private account identifiers or credentials are supplied.

# Progress

Updated 2026-11-04. Earlier milestone entries below are historical; see the latest completion entry for current connection status.

## Milestone 1

Runnable API, migrations, health checks, bootstrap, and separate HTTP services. PostgreSQL is the bundled server because Docker is absent.

Command: `python scripts/local_postgres.py` then `alembic upgrade head`. Result: database `cir` on 55432, revision `f4007edc4bf0`.

## Milestone 2

Deterministic missing-update path: investigate, separate approver, one support write, fresh verification, incident RESOLVED. Repeated in the browser on 2026-11-02.

## Milestone 3

Duplicate and stale events, CSRF, workspace isolation, forged approval, idempotency conflict, lost response, crash before dispatch, crash after the destination commit, login rate limit, logout revocation, pasted connector URL, stale proposal hash, destination revision conflict, append-only audit, and concurrent job claim.

The lost-response hang was a blocked stderr pipe. The handler now returns a completed 500.

## Milestone 4

HubSpot client: contact read, pagination helper, 429 retry, origin lock, freshness rule. Contract tests in `tests/unit/test_hubspot.py`. Live smoke: `python scripts/hubspot_smoke.py` exits 2. No token is stored.

## Milestone 5

LangGraph graph with read tools, runbook search, checkpoint tables, and deterministic fallback. Four runbooks are loaded, including `runbooks/imported-note.md`, which contains instruction-like text and is not followed. Live provider is not connected.

## Milestone 6

Seed 20261102. Frozen splits stay 39/39 for the rules, with citations checked against the plan text. A separate varied file scores 12/12 for the rules. The single-pass and tool-using runners execute against an injected fake and lose to the rules. Live mode stays pending unless the command is `python evaluations/run_baseline.py --live` and `CIR_EVAL_LIVE=1`.

## Milestone 7

React UI, Playwright desktop and mobile, telemetry trace endpoint, start/stop scripts, backup/restore, Compose and CI files, and these docs.

Logging fix and Ollama check, 2026-11-02: isolated suite 67 passed in 54.43s. Frontend build passed in 1.32s. API and worker restarted on build `2026.11.02.4`. The httpx status line now logs `200` without a format exception. Ollama's installed executable is unreadable, so live mode, the local-model browser repair, and the five-case comparison were not run. HubSpot settings are absent.

Container build: not run. `docker` is not on PATH. The CI workflow was updated and was not executed.

Next for Vinay: follow `docs/LEARNING_GUIDE.md`, then the HubSpot and local-model setup in `docs/INTEGRATIONS.md` if those free services are available.

## November 4 usability and runtime verification

Expanded the in-app Beginner guide with the business problem, pipeline, separate roles, three practice cases, and honest interview explanation. Approval controls are hidden after a recorded decision or repair. Demo scenario/reset actions now warn that shared simulator accounts (including practice accounts) are replaced and require confirmation; reset scope itself is unchanged.
Added scripts/check_setup.py, a read-only, secret-free local health/configuration report. Frontend build passed. Started local services successfully without resetting existing simulator records. Isolated browser learning verification passed (16.8s) in a fresh Verification November 4 workspace: approved repair, healthy no-op, unknown mapping refusal, no page errors. This run used deterministic rules and synthetic records, not a live model or HubSpot.
Ollama executable still fails corrupted/unreadable; no model server. No .env/model/HubSpot credentials configured. User clarified Astra refers to this chat model, not a separate API credential. User authorizes implementation/account setup in general but explicitly declines computer control for installer/browser steps. No accounts created, no model installation, no paid calls. Interactive setup remains pending.

## November 4 completion after renewed computer-control authorization

User subsequently authorized PC control, free HubSpot account creation/terms, and a read-only Service Key saved locally. Repaired Ollama using the valid-signed official installer (0.40.13); existing models preserved, `llama3:latest` active. Created free Northstar Recovery Test account, fictional contact/plan property, and contacts-read-only key in ignored `.env`. No card, paid purchase or hosted-model calls. The app only reads HubSpot; destination writes remain local.

Fixed JSON log formatting/redaction, standalone live-evaluation imports/config, evidence accumulation across model tool calls and required evidence reads before model reasoning. Ordinary tests now exclude local live credentials. Removed hardcoded false verification banners and clarified historical vs current repair UI. Created isolated HubSpot fixture and opt-in browser verification; existing Vinay Practice records preserved.

Verified: 70 unit/integration tests pass; frontend build pass; three-case real-local-model browser flow pass (32.2s); real HubSpot/local-model browser repair pass (28.7s). Final read-only inspection shows source Premium, support Premium, exactly one write, separate approval, VERIFIED/RESOLVED and one real model call (903 tokens). Build `2026.11.04.1`; local app http://127.0.0.1:8100. Evidence in ignored `var/live-integration-result.json` and screenshots.

Raw small-model evaluation remained weak (llama3 varied-five diagnosis 2/5 and four unsafe raw suggestions); preserved reports, added mandatory evidence prerequisites and policy guards, no autonomous/model-quality claim. Functional portfolio demo complete for the narrow plan-repair workflow; practitioner validation, hosted deployment, container/CI execution and proof of business impact remain open. Current guides and README updated for the working setup.

Final presentation verification: completed screen now separates historical investigation/approval data from verified current status. Imported raw live reports into Evaluations with explicit limitations and derived measured status; duplicate import adds no rows. API-only restart preserved data; read-only browser screen checks passed without page errors and final frontend build passed (1.27s). No additional repair or model call was made for these checks.
