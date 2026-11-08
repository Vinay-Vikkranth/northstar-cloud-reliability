# Provenance and contribution boundaries

This is an extension of Vinay Vikkranth Subramanian's existing Northstar Recovery portfolio application. It is AI-assisted implementation work. It is not iCliniq employer code, a customer system, or evidence of independent production ownership.

The copied starting point already had FastAPI, PostgreSQL, LangGraph checkpointing, React, human approvals, conditional writes, idempotency receipts, lease heartbeats, recovery after several crash points, a read-only HubSpot adapter, OTel export support and 70 unit/integration tests. Those are inherited capabilities, not new achievements of this extension. `INHERITED_SOURCE_MANIFEST.json` records SHA256 hashes of the 125 selected starting files. The source folder was read only; `.env`, runtime records, old evaluation responses, environments and screenshots were not copied.

The November 8 extension adds per-claim UUID fencing, expiry-aware compare-and-set renewal and completion, a pre-dispatch current-lease guard, a distinct dispatch owner per claim, tenant-scoped recovery sweeps, trace context persistence through the queue, HTTP propagation and correlated structured logs. It adds operations/SLO reporting, bounded audited dead-letter replay, an Operations UI, isolated portable native startup, new failure tests, measured scripted workload benchmarks, release integrity/rollback tooling and an unactivated cloud deployment recipe.

## Why PostgreSQL rather than another orchestrator

The existing application already persisted jobs, approvals, repair intents and LangGraph checkpoints in PostgreSQL. Keeping one durable state store permits a direct comparison with the starting implementation and avoids running a second control plane solely for a portfolio label. Temporal's official samples informed the durable activity, replay and observability review; **Temporal is not implemented or claimed here**.

This design is not a general replacement for Temporal. Long-running arbitrary workflows, workflow definition evolution, cross-region failover and an event-sourced replay engine are outside this application's scope. The queue provides at-least-once execution; conditional writes and immutable receipts provide the duplicate-side-effect boundary for the single allowed repair.

## References and rights

- PostgreSQL: [`SELECT ... FOR UPDATE SKIP LOCKED`](https://www.postgresql.org/docs/current/sql-select.html). The official docs specifically distinguish queue consumers from consistent general-purpose reads.
- OpenTelemetry: [Python propagation](https://opentelemetry.io/docs/languages/python/propagation/) and [Python getting started](https://opentelemetry.io/docs/languages/python/getting-started/).
- Temporal: [official Python samples](https://github.com/temporalio/samples-python), MIT-licensed reference, not copied into this repository.
- FastAPI: [static files](https://fastapi.tiangolo.com/tutorial/static-files/) and [lifespan](https://fastapi.tiangolo.com/advanced/events/).

No third-party sample source was pasted into this extension. Imported dependencies retain their respective licenses. The inherited project did not contain a repository-wide license. Publication here does not silently relicense inherited work, grant rights over employer material, or imply that all lines were independently authored by Vinay.

The real HubSpot key and test account records remain outside the repository. Ordinary tests and the default demo forcibly clear live CRM and model settings. The optional inherited HubSpot adapter only reads; no new permission or real-account write is part of this implementation.
