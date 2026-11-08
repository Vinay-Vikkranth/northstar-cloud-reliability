# Recovery and operations runbook

## First response

1. Open **Operations** in the affected workspace. Record job ID, state, attempts and trace ID. Its API never returns job payloads or raw error text.
2. Check `/health/live` for process/build and `/health/ready` for database connectivity. API readiness does not prove that a worker is healthy.
3. Search the local JSON logs for the trace ID. Follow the same ID through the API request, queued job and connector. Never paste credential files or raw HubSpot responses into an issue.
4. Inspect the destination receipt before deciding whether a timed-out repair should be attempted again. A lost HTTP response is not proof that no write occurred.

## Worker termination or missed heartbeat

Jobs are claimed in a short transaction with `FOR UPDATE SKIP LOCKED`. Each claim increments attempts and receives a fresh UUID token. A worker that dies leaves a durable lease; a later worker can reclaim it after expiry. Renewal requires the matching owner/token and an unexpired lease. Completion also checks the token and expiry. Heartbeat failure makes the pre-dispatch lease guard fail closed.

A guard cannot recall an HTTP request already in flight. The destination therefore enforces both the expected record revision and the idempotency key/payload receipt. This project claims one verified side effect in the tested destination, not exactly-once arbitrary distributed execution.

The test suite terminates an actual child process after a durable claim, then proves a new claim with a different UUID. Inherited process-crash tests also exit the worker after intent creation, destination dispatch, SUCCEEDED and RECONCILING, then verify without a second destination write. Those inherited tests remain credited to the starting project.

## Approval waits and restarts

Approvals are database records bound to the proposal hash, target values and source/destination revisions. Restarting the API or worker does not grant approval. If evidence changes, the proposal expires, a user rejects/cancels, or current permissions no longer hold, the executor must not start a new repair. Use a separate operator and approver when demonstrating the full boundary.

## Dead-letter review

A queue alert is displayed as soon as a job exhausts its attempt budget. Inspect its cause before replay. An administrator may explicitly request:

```http
POST /api/v1/operations/jobs/{job_id}/replay
X-Workspace-Id: <selected workspace>
X-CSRF-Token: <current session CSRF token>
Cookie: <current authenticated session>
Content-Type: application/json

{"reason":"Connector configuration reviewed and repaired"}
```

Only a dead job in the administrator's workspace is eligible. Attempts are retained, the queue budget grows by at most five up to a ceiling of twenty, and the audit identifies the actor and reason. The original repair intent's dispatch budget, approval and preconditions are **not reset**. Repeated clicks against a pending/done job return409.

## SLO interpretation

The local demo objective is terminal job success >=99% and last successful execution p95 <=5000ms. It requires at least ten terminal jobs and ten duration samples before reporting an objective result. Maintenance recovery sweeps are excluded. `done / (done + dead)` is an eventual terminal outcome ratio, not per-attempt HTTP availability. Approval waiting time is excluded from execution duration. Readiness load is a separate measured benchmark. A dashboard alert is not an external pager delivery; no external notification channel is configured.

## Release and rollback

Use `python scripts/release.py --help` for the content-hashed release workflow. Verify a release before activation and keep the previous immutable release. Roll back application images/source, not an in-use database directory. The new migration is additive; application rollback retains those columns. Do not downgrade the database merely to roll back an app release. Back up and test restoration before any destructive schema change.

For a cloud VM use the separate deployment recipe in [`../deploy/README.md`](../deploy/README.md). It is not activated and has no cost or availability claim. Local SQLite substitution is not used to claim PostgreSQL locking behavior.
