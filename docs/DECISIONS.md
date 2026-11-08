# Decisions

| Date | Decision | Why |
| --- | --- | --- |
| 2026-11-02 | Native PostgreSQL via pgserver on port 55432 | Docker is not installed. Compose remains the documented container path and was not booted. |
| 2026-11-02 | Web 8100, API 8101, CRM 8111, support 8121 | Avoid the existing ResolveDesk process on port 8000. Scripts do not kill foreign processes. |
| 2026-11-02 | Deterministic investigator unless `CIR_INVESTIGATOR_MODE=live` | Offline tests must not call a paid model. |
| 2026-11-02 | One OpenAI-compatible chat adapter | Covers a local server such as Ollama and a hosted compatible API without a second client. |
| 2026-11-02 | HubSpot reads one contact property and `lastmodifieddate` | Official CRM v3 contact read. No custom property is created by this project. Missing `lastmodifieddate` blocks a repair. |
| 2026-11-02 | Lost response returns HTTP 500 after the receipt commit | Raising an exception filled the test process stderr pipe and stalled the single worker. The receipt status stays 200. |
| 2026-11-02 | Rules labels were written from the same repair policy as the code | The 39/39 score is a regression check, not a hidden or real-world accuracy result. Live approaches stay pending. |
| 2026-11-02 | `northstar.combined` may investigate and approve | Labeled demo compromise so one person can practice. The browser test uses two different users. |
| 2026-11-02 | `sync_retry` records the event and does not reapply it | The operator reconciles. A silent second apply would hide a failed delivery. |
| 2026-11-02 | The repair policy stays in `diagnose`, and model text is only a competing explanation | A live or fake model can choose read tools. It cannot choose the plan that is written. |
| 2026-11-02 | Proposal decision state is a column with a narrower trigger | The old append-only trigger blocked cancellation, supersede, and stopping a prepared repair. Content fields stay immutable. |
| 2026-11-02 | Retry budget lives on the repair intent | A new queue job must not reset the count. Five attempts or fifteen minutes, with Retry-After capped at 30 seconds. |
| 2026-11-02 | Tests use ports 18111 and 18121 and databases `cir_test`, `crm_test`, `support_test` | The demo can keep 8111, 8121, and `cir` while the suite runs. |
| 2026-11-02 | A run is checked at job start, after each tool, and before the proposal insert | Cancelling the queue is not enough, because a worker may already be executing. |
| 2026-11-02 | Retrieved runbook text is sent to the model as untrusted data | The model can read the procedure. `diagnose` still chooses the written plan. |
| 2026-11-02 | Budgets are checked again after each provider response | A final response can exceed the ceiling. A missing price is unknown, not zero. |
| 2026-11-02 | Live evaluation runs only with `--live` | `CIR_EVAL_LIVE=1` alone does not make the default command call a provider. |
| 2026-11-02 | Missing model price follows `CIR_COST_POLICY` | `strict` rejects it. `local_unmeasured` keeps the other limits and labels money unmeasured. `estimated` uses configured token rates and labels the estimate. None of these stores an omission as zero. |
| 2026-11-02 | Live mode stays off while `ollama.exe` cannot start | The `llama3.2:latest` files are on disk, but the installed binary is unreadable. Turning on live mode would send investigations to a closed port. |

Audit rows are append-only at the database permission layer. That is not a claim of tamper-proof storage.
