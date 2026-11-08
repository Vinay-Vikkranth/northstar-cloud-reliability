# Architecture

The browser talks only to the recovery API. The investigator can read. The executor is the only component that writes, and it writes only the support account's `plan` field after a matching human approval.

```text
React (8100) -> FastAPI (8101) -> PostgreSQL cir (55432)
                     |                |
                     |                +-- LangGraph checkpoints
                     +-> worker
                     +-> CRM simulator (8111) or HubSpot read adapter
                     +-> support service (8121)
```

CRM and support keep their own databases (`crm`, `support`) and their own roles. The recovery role `cir_app` cannot connect to them. Writes to support send `Idempotency-Key` and `If-Match`. The same key with the same payload replays the stored receipt. The same key with a different payload returns 409. A 500 after the receipt is stored is recovered by reading that receipt, not by inventing a second plan change.

Incident states: OPEN, INVESTIGATING, NEEDS_INFORMATION, AWAITING_APPROVAL, EXECUTING, VERIFYING, RESOLVED, ESCALATED.

Repair states: PREPARED, DISPATCHING, SUCCEEDED, FAILED, OUTCOME_UNKNOWN, RECONCILING, VERIFIED, ESCALATED.

The proposal hash is SHA-256 of the canonical JSON for the account, field, before, after, preconditions, evidence ids, expiry, and version. Approval must send that hash. A graph checkpoint never authorizes the write. An open proposal can be approved, rejected, cancelled, or superseded. After approval it can still be rejected while the repair intent is PREPARED. Content fields stay immutable. A committed write is verified again after a crash; that path does not send another patch. Rate-limit and unavailable responses retry at most five times or fifteen minutes, and Retry-After is capped at 30 seconds.

A cancelled or replaced investigation is checked when its job starts, after each read, and again under a row lock before a proposal is stored. Retrieved runbook text goes to the model with its id, title, and version, marked untrusted. It does not choose the written plan. A live evaluation is a separate `--live` command.

The CRM and support simulators share fixed customer ids and an admin reset. They do not enforce workspace tenancy. The recovery API isolates rows by workspace and returns 404 for the wrong workspace. Connector health for a HubSpot source reports whether that token is configured; it does not call the demo CRM.

Roles: administrator implies the others. Operator and approver each imply viewer. Approver does not imply operator. Two workspaces are seeded: `northstar-demo` (synthetic, demo reset allowed) and `harbor-isolation` (demo reset refused). A wrong workspace gets 404.

Workflow version: `inv-2026.11.02`. Mapping version: `v1` (`basic`, `premium`, `enterprise`, plus starter, pro, and business aliases).
