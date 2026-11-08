HISTORICAL SOURCE DOCUMENT: copied from the earlier local Northstar project. Its former machine settings, live-account setup and evidence do not apply to a fresh clone of this release. Use README.md and docs/VALIDATION_2026-11-08.md for current setup, measured results and limitations. No old private account identifiers or credentials are supplied.

# Validation

## Current: November 4, 2026

Local API/worker build `2026.11.04.1`. This supersedes the historical blocked Ollama/HubSpot status below. Existing practice data was preserved.

| Check | Result | Evidence and scope |
| --- | --- | --- |
| Unit and integration suite | 70 passed, 3 warnings, 41.89s | Isolated synthetic services/test database; live credentials disabled for ordinary tests |
| Frontend | TypeScript and Vite build passed | `npm run build` in `apps/web` |
| Logging | JSON escaping and redaction tested | `tests/unit/test_telemetry.py`: quotes, newlines and exception secrets |
| Ollama | 0.40.13 running, installed `llama3:latest` used | Official signed installer repaired program; weights preserved; no paid API |
| Live AI / synthetic CRM browser | Three scenarios passed in 32.2s | Local AI Verification workspace: approved repair, healthy no-op, unknown mapping refusal |
| HubSpot smoke | status=ok, plan present, freshness established | One fictional contact in free developer test account |
| Real HubSpot + live AI browser | Passed in 28.7s, no page errors | Operator cannot approve; separate approver; local support repair verified |
| Final read-only inspection | RESOLVED / VERIFIED, exactly one support write | `var/live-integration-result.json`: source and destination Premium; no mismatch/duplicate; one model call, 903 tokens |
| Local services | Web, API, CRM, support and Ollama reachable | `scripts/check_setup.py` |
| Final presentation check | Passed, no page errors | Read-only browser visit verified resolved/history labels and live baseline cards; final frontend rebuild passed in 1.27s |

Imported the existing raw live report into the app using `scripts/import_live_evaluation.py`; a repeated import added zero duplicates. The Evaluations screen now derives live status from stored reports instead of a hardcoded pending label. Importing preserved the poor scores and made no model calls.

Local screenshots: `var/hubspot-before-approval.png`, `var/hubspot-verified.png`, `var/learning-verification/`. The key is only in ignored `.env`, with sole scope `crm.objects.contacts.read`. The application made no HubSpot writes. Setup created a fictional contact/property through the authorized HubSpot UI. No payment, paid upgrade or hosted model API was used.

### Model quality: measured limitation

`evaluations/results/live.json` is the five-case **raw model-led baseline** for local `llama3:latest`, run before mandatory evidence collection was added to the application. Single-pass and tool-agent diagnosis correctness were each **2/5**; next-action correctness **1/5** each. The tool agent had **1/5** valid citation results and **4 unsafe raw repair suggestions**. These were evaluation outputs, not executed repairs. This small sample does not estimate production accuracy. Earlier llama3.2 and development reports are retained beside it.

The raw runner does not invoke the application's mandatory evidence prerequisites. Do not relabel that baseline as guarded-application accuracy, or browser success as a broad accuracy score. The application first reads the source/destination and sync history, uses deterministic policy to establish the allowable change, requires separate human approval, and rechecks preconditions before writing. Model explanations may still be wrong. No measured evidence yet shows that the local model improves on rules for this narrow task.

### Remaining limits

- Functional local portfolio demonstration; no production deployment, customer adoption or measured business savings.
- Container boot and GitHub Actions remain unexecuted here. Practitioner validation is pending. No public deployment performed.
- Browser repairs consume their fixtures; use fresh isolated cases for future runs rather than resetting shared practice data.
- HubSpot is read-only; support is local. Developer test accounts can expire after inactivity.
- Local inference uses no billed model API, but monetary cost is recorded as **unmeasured**, not zero. Tool/token/time limits remain.
- Audit append-only permissions are not tamper-proof storage. Three FastAPI startup deprecation warnings remain.

## Historical: November 2, 2026 (superseded where noted above)

Commands below were run on 2026-11-02 after the logging fix. This project's API, worker, web, CRM, and support processes were stopped and started again. PostgreSQL on port 55432 was left running. Seed and demo reset were not run. `%USERPROFILE%\.ollama\models` was not changed. No unrelated service was stopped. No model was downloaded or called.

## Release statuses

1. Offline engineering: the isolated suite and frontend build passed on this machine. A live-model browser repair was not run. Container boot was not run.
2. External CRM: blocked. There is no `.env`, so the HubSpot token, contact id, and plan property are absent. No HubSpot request was made.
3. Live AI: blocked. The installed `ollama.exe` is unreadable. Port 11434 is closed. `llama3.2:latest` is present on disk but was not loaded. Live mode was not enabled. No model call was made.
4. Practitioner validation: pending. No review was requested or recorded.
5. Public deployment: not done and not authorized.

## Ledger

| Feature | Mode | Evidence | Result | Limitation |
| --- | --- | --- | --- | --- |
| Isolated unit and integration suite | synthetic HTTP + `cir_test` | `.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q --tb=line` | 67 passed, 3 warnings, 54.43s | Includes the round-2 probes, cost-policy tests, and `tests/unit/test_telemetry.py`. |
| Frontend build | local | `npm run build` in `apps/web` | passed, 1.32s | `tsc --noEmit` and Vite. |
| Logging filter | unit test and restarted worker | `tests/unit/test_telemetry.py`; `var/logs/recovery.log` | numeric httpx line rendered `200 OK`; no `Logging error` in the new `worker.err` | Secret-bearing messages are still redacted. |
| Restarted API | local process | `GET http://127.0.0.1:8101/health/ready` | `{"status":"ready","build":"2026.11.02.4"}` | Web returned HTTP 200. |
| Restarted worker | local process | `var/logs/recovery.log` | `worker started build=2026.11.02.4` | A deterministic investigation then logged CRM and support reads with status 200. |
| Approval replay and queued cancel | restarted API, synthetic | `.venv\Scripts\python.exe scripts\verify_restart.py` | `replay_ok write_count=1`; `cancel_before_start_ok attempt=1` | A second approval returned 409. This is not a live-model run. |
| Ollama | local binary | `ollama --version` | failed: file or directory is corrupted and unreadable | Installer at `C:\Users\svina\Downloads\OllamaSetup.exe` has not been run. Model files were preserved. |
| Live model comparison | not run | — | blocked | Five-case local scoring was not run. Fake-provider scores are not a substitute. |
| HubSpot smoke | not run | no `.env` | blocked | The three HubSpot settings are absent. |
| Container boot | Docker Compose | not run this round | still unverified | Docker was not installed on the earlier check. Do not treat Compose as passed. |
| GitHub Actions | CI file | `.github/workflows/ci.yml` | not run | Do not treat the workflow as passed. |
| Practitioner | human | none | pending | — |
| Public URL | hosting | none | not deployed | — |

`CIR_COST_POLICY=strict` is the default. A provider that omits `cost_usd` stops the loop, and the omission is not stored as zero. `local_unmeasured` is the explicit local-server policy: money is labeled unmeasured while token, tool, response-size, and elapsed limits remain. `estimated` records a labeled estimate from `CIR_INPUT_USD_PER_MILLION` and `CIR_OUTPUT_USD_PER_MILLION` and still applies the dollar ceiling. The same policy is used by live evaluation. The default `python evaluations/run_baseline.py` command was not re-run this round; its previous offline scores remain the last measured contract numbers.

The isolated suite still covers the round-2 probes. `npm run test:e2e` was not run because that spec resets the demo workspace. The headless live-model repair is waiting on a working Ollama server.

`sync_retry` records the event and does not send a second support write. That is covered by code inspection, not by a dedicated passing test.

The CRM and support simulators still share fixed customer ids. Workspace isolation is enforced in the recovery database, not inside those simulators.

Audit rows are append-only at the database permission layer. That is not a claim of tamper-proof storage. FastAPI still warns that `@app.on_event("startup")` is deprecated.
