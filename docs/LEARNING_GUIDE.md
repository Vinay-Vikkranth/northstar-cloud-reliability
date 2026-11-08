# Learning guide

## The problem

A customer's plan changes in the CRM. The support account still shows the old plan. Guessing, or writing every field, can make that worse. This app reads both records, explains the disagreement, and writes only `plan` after a person approves the exact before and after.

## The pieces

| Piece | Where | Job |
| --- | --- | --- |
| Browser | `apps/web/src/App.tsx` | Queue, diff, approval, verification |
| API | `apps/api/recovery_api/routes.py` | Auth, incidents, approval |
| Worker | `apps/worker/recovery_worker/main.py` | Claims jobs |
| Diagnosis | `packages/recovery_core/recovery_core/diagnosis.py` | Rules, no model required |
| Graph | `apps/api/recovery_api/investigate.py` | Read tools and a checkpoint |
| Executor | `apps/api/recovery_api/execute.py` | The only writer |
| CRM / support | `services/crm_simulator`, `services/support_service` | Independent HTTP apps |

## One incident, from the button to verification

1. The operator clicks Investigate. `investigate` in `routes.py` inserts an `InvestigationRun` and a job.
2. The worker calls `run_investigation`.
3. The graph in `investigate.py` calls `get_source_customer`, `get_destination_customer`, `list_sync_attempts`, and `search_runbooks`. Tool results are data. `runbooks/imported-note.md` tells the model to change the plan to enterprise. The rules ignore that and keep the mapped value.
4. `diagnose` returns `missing_update` and `propose_repair` when the plans differ and the support account has not applied the CRM revision.
5. `build_repair_proposal` stores the hash. The incident becomes `AWAITING_APPROVAL`. The graph's `wait` interrupt does not write.
6. The approver posts the hash to `approve`. `_decide` checks the hash, expiry, and role, then inserts a `RepairIntent` in `PREPARED` and a job.
7. `execute_intent` reads both systems again. If either changed, it does not write. It moves the intent to `DISPATCHING` and commits. `CIR_CRASH_POINT=after_intent` exits here, before HTTP.
8. It patches support with the idempotency key and `If-Match`. `CIR_CRASH_POINT=after_dispatch` exits after that response.
9. `_verify` reads both systems again. The incident is `RESOLVED` only when the fresh support plan matches and the source fingerprint is unchanged.

A database transaction cannot undo the support HTTP call. The receipt is how a retry knows the first call landed.

## Exercises

1. Mapping: add a harmless alias in `packages/recovery_core/recovery_core/mapping.py` and a unit test in `tests/unit/test_domain.py`. Do not silently map an unknown value to a paid plan.
2. Timeout: change the `timeout=3.0` on the support client in `execute.py`, then explain which failures become `OUTCOME_UNKNOWN`.
3. Failure test: copy `test_worker_crash_does_not_duplicate_the_write` and assert `write_count` stays 1.
4. Stale proposal: approve with 64 `b` characters. The API returns 409 `stale_proposal` from `_decide`.
5. Wrong diagnosis: if support says it applied the CRM revision but the plans differ, the cause is `contradictory_evidence` and the next step is `escalate`, not a write.

## Interview answers grounded in this code

- Why use a model at all? The rules already classify this narrow task well. November 4 real local-model tests were weak; no measured improvement over rules is established. The model can suggest explanations and additional reads, but policy and a person control the repair. Showing that tradeoff honestly is more useful than claiming the model is necessary.
- What if the rules win? Report that. The baseline file says the rules scored 39/39 on synthetic policy labels. Do not hide it behind an agent diagram.
- What happens after a crash? The intent and the idempotency key are already committed. Restart calls the same key. Support replays the receipt.
- What is the source of truth? For the plan value, the two live reads. For whether a write may happen, the stored approval hash. The graph checkpoint is not an approval.
- How are customers isolated? Every query filters on `workspace_id`. Another workspace gets 404, including export. Demo reset checks the slug.
- Why can't a SQL transaction undo an external request? The support service commits its own row. Our rollback cannot reach it. The receipt is the shared memory of that commit.

## Contribution record

On November 2, 2026 a Cursor agent implemented this repository from `BUILD_SPEC.md`; Codex subsequently reviewed, repaired and tested it, including the November 4 real integrations. Vinay directed the project and authorized setup; his independent code understanding or implementation contributions have not yet been recorded. No practitioner session took place. Update this when he completes the walkthrough and makes explainable changes. Describe the work honestly as AI-assisted.

## Resume templates

Use these only after you can explain the code. Replace the brackets with your own measured notes. Do not invent the missing numbers.

- Built a plan-sync recovery app that reads a CRM and a support API, requires a second person to approve a hashed plan change, and verifies the write with a fresh read. Stack: FastAPI, PostgreSQL, React, LangGraph. Browser path covered [N] synthetic accounts.
- Compared deterministic diagnosis with synthetic policy cases and measured local-model failures; separated raw model suggestions from evidence-checked, human-approved writes. Use the exact sample sizes and results in `VALIDATION.md`.
- Connected a read-only HubSpot developer test account and local Ollama model; verified one approved local-support repair with a fresh source/destination check on November 4, 2026. This is a fictional test account, not a production customer integration.
