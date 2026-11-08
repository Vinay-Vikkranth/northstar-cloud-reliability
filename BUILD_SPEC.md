# Customer Integration Recovery — complete Cursor build specification

Prepared for Vinay on November 2, 2026. This document is a specification, not evidence of implemented features or measured results.

## 1. Instructions to the coding agent

Build the complete scoped application described here. Work through the milestones, run their checks, fix failures, and maintain progress records. Do not stop after scaffolding, a polished mockup, or a proposed plan. Do not describe unfinished or simulated features as real integrations.

Read this entire document before editing. Inspect the selected workspace, existing files and applicable AGENTS.md instructions. Use the folder containing this document as the project root unless Vinay explicitly selects another empty folder. Keep this specification in the repository. Do not overwrite another project or copy its private data/history. Existing ResolveDesk is a separate project; do not modify it to implement this one.

Use current official documentation to verify dependency APIs and the external provider's authentication, properties, pagination and limits. Pin compatible stable dependencies and commit lockfiles. Make routine implementation decisions independently and record significant trade-offs in docs/DECISIONS.md. Ask only for genuine blockers such as account credentials, an unavoidable cost or a missing user requirement. Continue independent work while a live-service blocker remains.

Never ask Vinay to paste secrets into chat. Document local environment setup instead. Do not send messages, modify personal accounts, publish a repository, deploy publicly, buy services or run paid model evaluations without explicit authorization. Local implementation, synthetic tests and packaging are the requested work. Existing subscriptions do not imply unrestricted API spending.

## 2. Purpose and truthful positioning

Create an original application that investigates and safely repairs failed customer-record synchronization. It should demonstrate the engineering work of an application-focused Forward Deployed Engineer: problem framing, API integration, AI investigation, authorization, failure recovery, evaluation, deployment and handoff.

We are not rebuilding Instant, a database engine, or a general integration platform. Instant is an inspiration for thinking about synchronization and state. Using Instant is not required. Do not fork it and rename it. Credit any reused libraries, examples or assets and honor their licenses.

This is initially a portfolio system using fictional customer records. It becomes a validated customer pilot only after actual practitioner feedback and documented use. Never invent adoption, savings, production scale, AI accuracy or hiring outcomes.

## 3. Customer problem and narrowly defined workflow

Fictional company: Northstar SaaS. Its CRM is the source of truth for subscription plans. A separate support account service uses that plan to show support entitlements.

Example: Alex's plan changed from Basic to Premium in the CRM, but the support account still shows Basic. An operator needs to know whether the mismatch is real, which source is authoritative, what prevented the update, and whether retrying is safe.

Initial scope: synchronize one account's `plan` and a stable CRM customer identifier. Use a small explicit enum such as basic/premium/enterprise. A locally versioned mapping can translate external values. Do not use email alone as an identity key. No billing charges, refunds, account deletion or broad record rewrites.

The application must:

1. Detect a mismatch through periodic reconciliation or accept an operator-reported incident.
2. Read source and destination records, recent sync attempts and relevant runbooks.
3. Diagnose with cited evidence; represent insufficient or conflicting evidence honestly.
4. Produce an exact repair proposal showing before/after values and preconditions.
5. Require approval from an authorized human.
6. Execute through a restricted repair service, not through free-form model commands.
7. Re-read the destination and source to verify the result.
8. Keep an attributable audit trail and escalate uncertain outcomes.

Success means verified convergence with no unintended changes, not merely receiving HTTP 200.

## 4. Integration strategy and execution modes

Use two separately running HTTP systems, not two tables secretly treated as external integrations.

### A. Fully reproducible offline mode — mandatory

- `crm-simulator`: realistic independent HTTP CRM, synthetic accounts, pagination, revision tokens, signed events and configurable faults.
- `support-service`: independent destination service with account revisions, conditional updates and durable idempotency receipts.
- Recovery application connects over HTTP to both. Stop/restart either process independently in tests.
- Never expose a simulator's hidden fault label or expected answer to the investigator. Fault controls belong to an administrator-only demo interface, unavailable through agent tools.
- UI must visibly say **Synthetic demo**. No model key required; deterministic investigation is available.

### B. Real external CRM mode — mandatory adapter, credentials-dependent verification

Implement a HubSpot CRM adapter as the first real external source, using a dedicated developer/test account and fictional contacts. Verify the currently supported test-account and authentication options in official documentation. Read only the minimum needed contact fields: stable ID, configured plan property, modification metadata and any required mapping information.

HubSpot is the real external source; the support destination remains our own demo service. State that distinction prominently. Do not call this a live HubSpot-to-Zendesk integration.

- Configure the plan property name; do not assume a custom property already exists.
- Supply precise account/property setup and permission instructions; do not silently create properties in a real account.
- Handle pagination, invalid/expired credentials, rate limits and unexpected payloads.
- Do not log bearer tokens or full customer payloads.
- If provider version semantics cannot support a strict snapshot, use explicit fingerprint/re-read checks, document their limits, and refuse unsafe execution when freshness cannot be established.
- Integration reads are sufficient for the first external-source demonstration. Our repair writes go to the support service.
- No credentials: adapter must be implemented and contract-tested against sanitized fixtures, but live verification is marked BLOCKED. Offline completeness is still testable.
- Provide an opt-in live smoke command that reads only the configured test account and reports actual results. No automatic account-wide scans.

### C. Live AI mode — mandatory adapter, credentials/budget-dependent verification

Use one configurable LLM provider initially. Select the model at execution time from available account options and compare actual results; do not hard-code an unverified claim that one model is best. Default offline mode must never secretly make paid calls.

If credentials/budget are unavailable, fake-provider contract tests can verify orchestration, but do not label those tests as live AI evaluation. Show the active provider/mode in the UI and every evaluation report.

### D. Practitioner validation — independent completion status

Prepare a feedback checklist and pilot script. Actual feedback needs a real person. Mark this pending unless evidence exists. Do not invent a user interview to satisfy completion criteria.

## 5. Technology choices

- Frontend: React, TypeScript, Vite, accessible components and a restrained consistent styling system. Use a maintained icon library, no generated decorative assets required.
- Backend: Python, FastAPI, Pydantic, SQLAlchemy and Alembic migrations.
- Persistence: PostgreSQL for incidents, evidence, repair intent, approvals, audit and worker jobs. Separate databases/roles for the main app and simulated services; services do not directly query each other's tables.
- Workflow: LangGraph with persistent checkpoints for investigation and human interruption. Domain tables remain the source of authority for approvals and repairs. A graph checkpoint alone must never authorize a write.
- LLM access: one provider adapter with validated structured responses and bounded tools; no mandatory multi-provider routing.
- Retrieval: a small versioned runbook corpus with PostgreSQL full-text search or a straightforward lexical retriever. Add vector search only if a measured requirement justifies it.
- Background execution: a PostgreSQL-backed worker with atomic claim/lease, bounded retries and restart recovery. Avoid adding Redis, Kafka or a second workflow platform without need.
- Observability: structured logs and OpenTelemetry traces with a local trace viewer. Exclude secrets and sensitive content from telemetry.
- Tests: pytest, real PostgreSQL integration tests, HTTP service tests and Playwright browser tests.
- Packaging: Docker Compose, health checks, environment templates and cross-platform start/stop scripts. CI through GitHub Actions if a repository is later connected.

These tools are proposed implementation choices. Verify current compatibility. Prefer a small understandable architecture over unnecessary services.

## 6. System architecture and boundaries

```text
Operator browser
      | authenticated API
FastAPI application ------ PostgreSQL (domain records, jobs, audit)
      |                                |
      +---- investigation worker ------+
                 |
              LangGraph
                 +---- restricted read tools ---- CRM adapter ---- simulator OR HubSpot
                 +---- restricted read tools ---- support-service
                 +---- runbook retrieval
                 +---- configured LLM adapter (optional live mode)

Authorized approval -> validated repair intent -> restricted executor -> support-service
                                                         |
                                                  verify/reconcile

App / worker / services -> redacted traces and metrics -> local observability viewer
```

The investigator cannot issue writes. The executor cannot accept arbitrary model-generated URLs, SQL, code, property names or HTTP methods. Connector hosts and writable fields are configured server-side and allowlisted. Never execute text retrieved from a runbook or customer record as instructions.

## 7. Roles and trust model

Mandatory application roles:

- Viewer: read incidents within assigned workspace.
- Operator: create/investigate incidents and propose permitted repairs.
- Approver: approve/reject exact proposals within assigned workspace.
- Administrator: manage local users, connector configuration and synthetic demo fault controls.

Use real backend authentication and server-side authorization. For local use, implement named users with securely hashed passwords and expiring/revocable server-side sessions, secure cookie settings appropriate to HTTP localhost versus HTTPS, CSRF protection and login rate limiting. Never ship an active universal default admin password. Provide a local one-time bootstrap command.

An operator may also hold approver permission for a small demo, but the approval must remain an explicit attributable action. Document this compromise. Include tests with distinct users.

Have workspace IDs and enforce access isolation on every lookup, tool call, approval, export and worker action. Include two synthetic workspaces to prove isolation. This is scoped logical isolation, not a claim of independently certified multitenancy.

## 8. Domain data model

Implement migrations, constraints, useful indexes and UTC timestamps. Display time zones explicitly.

- Workspace, User, Membership, Session.
- ConnectorConfig: provider, fixed origin, workspace, secret reference, capability flags and last health status; no secrets returned to browser.
- CustomerLink: workspace, source connector/customer ID, destination customer ID, mapping version. Unique identity constraints.
- SyncEvent / SyncAttempt: stable event ID, source revision, delivery state, timestamps, attempt count and sanitized response evidence.
- Incident: customer link, mismatch description, state, severity, owner and active investigation.
- InvestigationRun: mode/provider/model, workflow version, state, budget, timing and trace ID.
- Evidence: source, observed timestamp, sanitized content, content fingerprint, revision if available, sensitivity classification and immutable evidence ID.
- Diagnosis: cause enum, evidence IDs, competing explanations, limitations and next action. A displayed certainty category is not a calibrated probability.
- Proposal: exact allowed field diff, source/destination preconditions, connector identity, mapping version, evidence IDs, expiry and immutable proposal hash/version.
- Approval: proposal hash, approver, decision, timestamp and reason. No approval of mutable proposal content.
- RepairIntent: proposal/approval references, unique idempotency key, immutable payload hash, state and reconciliation metadata.
- RepairAttempt: outgoing operation, response classification, timing and destination receipt ID.
- Verification: fresh observations, mismatch/duplicate checks, outcome and limits of what was checked.
- AuditEvent: actor, workspace, action, resource/version, timestamp, trace and sanitized metadata.
- WorkerJob: state, lease owner/expiry, attempt limit, heartbeat and scheduling time.

Audit is application append-only with restricted DB permissions where practical; do not claim cryptographic tamper-proof storage.

## 9. State machines and correctness requirements

Incident states: OPEN -> INVESTIGATING -> NEEDS_INFORMATION / AWAITING_APPROVAL -> EXECUTING -> VERIFYING -> RESOLVED / ESCALATED. Rejection and expired proposals remain visible. Do not force every diagnosis to offer a repair.

Repair states: PREPARED -> DISPATCHING -> SUCCEEDED / FAILED / OUTCOME_UNKNOWN -> RECONCILING -> VERIFIED / ESCALATED.

Mandatory execution protocol:

1. Recheck actor permissions, workspace, approval hash, proposal expiry, connector identity and permitted fields.
2. Re-read source and destination; reject stale/conflicting proposals. Never silently adapt an approved payload.
3. Persist immutable repair intent before making an external write.
4. Claim execution atomically to prevent two workers executing the same intent.
5. Send a stable idempotency key and expected destination revision to support-service.
6. Support-service atomically checks revision, writes the allowed change and stores the idempotency receipt. Same key/same payload returns the same result; same key/different payload is rejected.
7. If timeout/process death occurs after dispatch, keep outcome unknown; inspect the receipt and actual destination state before any retry. Never assume timeout means failure.
8. Verify destination state and fresh source authority. If the source changed concurrently, do not call the incident permanently resolved; reopen/reconcile as appropriate.

PostgreSQL transactions protect local state only. They cannot atomically roll back another HTTP service. Explicit reconciliation is essential. Do not claim distributed exactly-once delivery; demonstrate deduplication, conditional writes and recovery under documented assumptions.

Credentials cannot be repaired by asking the model to invent or extract a token. An expired connection requires an authorized reconnect; the workflow then re-investigates before proposing a write.

## 10. AI investigator and tool contracts

Build a bounded investigator that can adapt evidence gathering to the incident. A cosmetic explanation over a hidden scripted answer is insufficient.

Read-only tools, all scoped and validated server-side:

- get_source_customer(customer_link_id)
- get_destination_customer(customer_link_id)
- list_sync_attempts(customer_link_id, bounded_time_window)
- get_connector_health(connector_id)
- search_runbooks(query, bounded_limit)
- compare_records(customer_link_id)

Agent output must conform to a schema: cause, factual observations with evidence IDs, hypotheses, missing information, recommended next step and candidate repair type. Backend constructs the executable proposal from current authoritative records and an allowed mapping; model output cannot directly authorize or define arbitrary writes.

Reject nonexistent citations, malformed output, unsupported fields and wrong-workspace evidence. Allow at most a bounded validation retry, then safely fall back/escalate. Set configurable limits for tool calls, elapsed time, tokens and estimated cost. Record actual provider usage when supplied; label estimates. Handle model unavailability and cancellation.

Runbooks, logs and CRM fields are untrusted content. Test malicious instructions embedded in each. The agent must not reveal secrets, access other customers or use unrestricted network tools. Do not persist hidden chain-of-thought; retain tool observations and concise decision summaries.

Use a deterministic investigator in offline mode. Clearly label it. The live agent must be evaluated separately so offline success is never presented as model accuracy.

## 11. Failure scenarios

At minimum implement independently configurable faults:

| Scenario | Expected behavior |
|---|---|
| Missing update event | Reconciliation detects mismatch; reviewed replay converges |
| Duplicate event | No duplicate mutation or customer |
| Out-of-order older event | Cannot overwrite a newer source state |
| Destination 429 | Respect bounded Retry-After/backoff; no tight retry loop |
| CRM credentials expired | Show missing access; require reconnect; no invented diagnosis |
| Destination outage | Preserve job/intent; resume safely after service restart |
| Source field rename / mapping mismatch | Identify mapping issue; require explicit corrected version, then re-investigate |
| Source changes after approval | Reject stale proposal and obtain fresh approval |
| Destination changes after approval | Conditional-write conflict; preserve newer state |
| Write succeeds, response lost | Recover receipt; no duplicate write |
| Worker dies just before/after dispatch | Durable recovery with bounded retries/reconciliation |
| Unauthorized or wrong-workspace request | Deny without leaking customer evidence |
| Unknown customer / ambiguous identity | No write; request explicit mapping |
| Contradictory or insufficient evidence | Explain uncertainty and escalate |
| Invalid model response / model unavailable | Validated fallback or escalation; preserve state |
| Malicious instructions in retrieved text | Treat as data; preserve tool and write boundaries |

Do not resolve all failures with “retry.” Mapping changes, access failures and uncertainty have different remedies.

## 12. API surface

Design versioned typed endpoints, documented in OpenAPI. Suggested resources:

- /api/v1/auth: login, logout, current user; bootstrap through local CLI only.
- /api/v1/incidents: list/filter/create/detail, investigate, cancel, reopen.
- /api/v1/investigations/{id}: status, evidence, summary, tool timeline.
- /api/v1/proposals/{id}: detail, approve, reject. Approval requests include version/hash.
- /api/v1/repairs/{id}: execution state, attempts, reconciliation and verification.
- /api/v1/connectors: sanitized status/configuration with restricted admin changes.
- /api/v1/events/{connector}: authenticated/signed webhook ingestion with event deduplication.
- /api/v1/evaluations: report metadata/read-only results; launching paid evaluations is opt-in.
- /health/live and /health/ready: do not reveal secrets or internal stack traces.

Use meaningful status codes and structured errors; pagination, request IDs and safe input limits. For long workflows use background jobs and polling or authenticated SSE. If webhooks are not supported by the chosen external account, use explicit bounded polling and document it rather than faking live events.

## 13. User experience and presentation

Create a polished responsive product, not a wall of technical logs. Use consistent spacing/type/color, accessible contrast, keyboard navigation, visible focus, labeled fields and useful empty/loading/error states. No dead controls.

Views:

1. Incident queue: status, account, severity, owner, age, filters/search and honest summary counts.
2. Incident workspace: plain-language problem, side-by-side source/destination values, investigation progress and evidence links.
3. Diagnosis: cause, supporting evidence, what is unknown and proposed next step.
4. Approval panel: exact before/after diff, affected account, permissions, age/preconditions and clear approve/reject actions.
5. Verification: actual fresh checks and remaining discrepancies; never a fabricated green success banner.
6. Activity history: readable audit events and repair attempts with optional technical detail.
7. Connector settings: connection state and safe setup guidance, no secret echoes.
8. Evaluation dashboard: measured results, sample size, mode, model/version, costs and failures.
9. Demo controls: admin-only, visibly synthetic; choose a scenario and reset the dedicated demo workspace.
10. Beginner guide: explain the customer problem and a guided walkthrough in plain language.

Show Synthetic/External CRM and Deterministic/Live AI modes conspicuously. Distinguish “repair accepted,” “repair verified” and “incident resolved.” Fake data may look realistic but must be labeled.

## 14. Evaluation design

Evaluate three approaches on the same allowed evidence and repair policy:

A. Deterministic rules baseline.
B. Single-pass retrieval-assisted LLM with a fixed evidence bundle.
C. Tool-using LangGraph investigator.

Approaches B/C require live model access to establish model quality. Fake responses test contracts only. Fairness matters: record actual model/context/tool/cost differences. If rules outperform the agent, report it and explain which cases merit AI.

Create at least 60 labeled synthetic scenarios: at least 30 development and 30 frozen evaluation cases, with multiple variants across failure categories. Include healthy/no-action and insufficient-evidence cases. Separate them by causal configuration or scenario family as far as practical, not random copies of the same template. Freeze the evaluation set before tuning final prompts; never put labels in searchable runbooks or tool responses. Record generator seed, split rationale and label provenance. Synthetic labels do not prove real-world accuracy.

Report:

- Diagnosis correctness and confusion matrix, including unknown/escalate.
- Evidence citation validity and whether cited observations support claims.
- Correct next action; unsafe repair proposal/execution counts.
- Repair convergence, duplicate writes, unintended field changes and stale-write rejection.
- Resolution/escalation counts and failure categories.
- Median/p95 latency, tool calls, tokens and cost with sample sizes and environment.
- Measured operator time only after an actual timed user study; do not convert synthetic latency into business savings.

Repeat live-model runs when budget allows and report variation. Keep machine-readable results with commit/config/model identifiers, plus a concise readable report. No invented target accuracy or fabricated benchmark. All critical safety/correctness regression cases must pass to call the offline release ready; report the exact cases and scope of the claim.

## 15. Test requirements

- Unit tests: mapping, identity, proposal hashes, expiry, authorization, schema validation and evidence references.
- PostgreSQL integration: migrations from empty DB, constraints, concurrent job claims, leases, state transitions and session revocation.
- Independent HTTP tests: timeouts, conditional writes, idempotency conflict, malformed success responses and lost responses.
- Crash tests: kill the worker after persisted intent and after destination commit; restart and prove recovery without duplicate mutation.
- Security regressions: cross-workspace reads/writes/exports/tools, forged approval, replayed/stale approval, CSRF, prompt injection and arbitrary connector URL attempts.
- Browser E2E: sign in -> find mismatch -> investigate -> inspect evidence -> approve -> verified outcome; rejection, escalation and permission failures too.
- Layout: desktop and narrow/mobile viewport, keyboard flow and no console errors.
- Live smoke: separately opt-in for real CRM and real LLM; no paid/provider calls in default CI.

Do not replace PostgreSQL with SQLite for tests of locking or concurrency. Mock network behavior where useful, but at least one complete path must run against separate actual services and PostgreSQL.

## 16. Repository structure

```text
BUILD_SPEC.md
README.md
.env.example
.gitignore
compose.yaml
apps/web/                 # React application
apps/api/                 # API, auth and domain services
apps/worker/              # durable jobs and workflow runner
services/crm_simulator/
services/support_service/
packages/                 # narrowly shared schemas/utilities if useful
migrations/
runbooks/                 # versioned fictional operator documentation
datasets/development/
datasets/evaluation/
tests/unit/
tests/integration/
tests/e2e/
evaluations/              # runners, configuration and result schema
scripts/                  # setup, seed, start/stop, smoke, evaluation
docs/ARCHITECTURE.md
docs/DECISIONS.md
docs/PROGRESS.md
docs/VALIDATION.md
docs/OPERATIONS.md
docs/INTEGRATIONS.md
docs/DEMO.md
docs/LEARNING_GUIDE.md
docs/PRACTITIONER_FEEDBACK.md
.github/workflows/ci.yml
```

Adjust layout if dependencies require it; preserve clear service boundaries. Avoid unnecessary packages or generated boilerplate. Exclude secrets, provider records, DB volumes and local reports containing private data from Git.

## 17. Delivery milestones — execute in order

### Milestone 1: inspect, initialize, make it runnable

Record scope and architecture, initialize dependency manifests/lockfiles, Compose, migrations, health checks and bootstrap authentication. Start all services and prove API-to-service HTTP calls. Document exact commands actually tested. Preserve any existing files.

### Milestone 2: deterministic end-to-end recovery

Implement records, reconciliation, incident queue, evidence capture, rules diagnosis, exact proposal/approval, executor and verification. Demonstrate a missing update through the browser. No LLM needed yet.

### Milestone 3: durable correctness and authorization

Add the concurrency, stale-state, idempotency, lost-response and crash-recovery cases. Add two workspaces and distinct users. Do not proceed on a known critical write-boundary failure.

### Milestone 4: real external CRM adapter

Implement HubSpot adapter and setup guide, sanitized contract fixtures, pagination and rate-limit behavior. Run opt-in smoke only when credentials are supplied. Maintain clear verified/blocked status.

### Milestone 5: AI investigation

Implement LangGraph state/checkpoints, bounded read tools, runbook retrieval, structured diagnosis, budget controls and injection tests. Connect a live provider when authorized. Preserve deterministic fallback and exact mode labels.

### Milestone 6: evaluate and improve

Freeze evaluation cases, run available baselines, inspect failures, and correct implementation defects. Do not tune against held-out results and continue claiming they were unseen; version a new split if necessary. Leave live results pending when unavailable.

### Milestone 7: polish, package and teach

Finish all UI states, accessibility, desktop/mobile browser checks, telemetry and operating scripts. Verify clean installation, stop/restart and CI configuration. Write a five-minute demo and beginner learning guide. Record limitations honestly.

After each milestone update docs/PROGRESS.md with implemented features, commands/results, blockers and next work. Continue until the scoped offline release is complete. Credentials-dependent work must remain explicitly labeled, not silently omitted.

## 18. Completion and validation ledger

Maintain a table in docs/VALIDATION.md with feature, mode, evidence/command, date, result and limitation.

Separate release statuses:

1. **Offline engineering release:** working synthetic separate-service workflow, durable approval/repair, complete UI, critical regressions, evaluation harness and reproducible packaging.
2. **External CRM verified:** actual authorized test-account reads and reconciliation demonstrated.
3. **Live AI evaluated:** actual model runs with recorded metrics and costs.
4. **Practitioner validated:** attributable feedback and observed user workflow, with permission and no private details published.
5. **Public deployment verified:** only after authorized hosting, HTTPS, configuration and smoke tests.

Never collapse these into one unsupported “production-ready” claim. A screenshot is not proof of integration; a unit test is not proof of live-model quality; a Dockerfile is not proof a container builds.

Offline release acceptance checklist:

- Clean setup from documented prerequisites works with no provider account.
- Seeded demo includes mismatches, healthy records and multiple fault types.
- All displayed UI actions are implemented and authorized.
- Evidence, proposals, approvals, repair intents and verification persist across restart.
- Critical stale-write, duplicate, lost-response, crash and isolation tests pass.
- Resolved incidents include actual fresh verification evidence.
- Three-approach evaluation runner exists; unavailable live runs clearly pending.
- Container build/boot and browser workflow have actually been executed, or specifically blocked.
- No secrets/private data in tracked files or logs.
- Documentation matches commands, ports, modes and current behavior.

## 19. Installation and operating experience

Provide one documented primary startup path using Docker Compose, plus native development instructions for Windows PowerShell and Unix shells. Check prerequisites and ports, wait for readiness, show the actual URL and return useful errors. Default application port should be configurable; prefer 8100 to avoid the existing ResolveDesk service on 8000. Never kill an unrelated process to free a port.

Keep background processes manageable, write logs to documented locations, and provide safe stop/restart commands that affect only this project. Demo reset must target the explicitly named synthetic workspace and never the external account. Include backup/restore instructions and test local restoration for domain data. Document pending workflows and destination receipt retention requirements.

Cloud deployment: prepare environment guidance and architecture, but do not deploy or provision billable infrastructure automatically. Hosting provider selection depends on budget and support for persistent workers/PostgreSQL.

## 20. Learning and interview deliverables

The project must be understandable to Vinay, who is learning these tools. docs/LEARNING_GUIDE.md must explain the customer problem, each service, an API request, database transaction, workflow checkpoint, approval, idempotency and reconciliation with examples from the actual implementation.

Include:

- A traced journey of one incident from browser to verification, linking actual code files/functions.
- A five-minute demo: show disagreement, inspect evidence, approve a repair and verify; then demonstrate a lost response without duplicate mutation.
- A longer technical walkthrough of trade-offs and limits.
- Exercises for Vinay: add a harmless mapping field, change a timeout, add a failure test, trace a stale proposal, and explain an incorrect diagnosis.
- Interview questions and answers grounded in actual code: Why use AI? What if rules win? What happens after a crash? What is the source of truth? How do you isolate customers? Why cannot a SQL transaction undo an external request?
- A candid contribution record showing AI assistance and Vinay's own changes/understanding as they occur. Do not invent personal authorship or customer experience.
- Potential resume bullets as templates with placeholders for measured results, not fabricated numbers. Only include technologies actually implemented and understood.

## 21. References and scope context

Technical reference: https://github.com/instantdb/instant — existing backend/database platform, not the project we are claiming to recreate.

Official documentation to consult during implementation:

- https://fastapi.tiangolo.com/
- https://docs.langchain.com/oss/python/langgraph/overview
- https://www.postgresql.org/docs/current/
- https://developers.hubspot.com/ — verify current account, auth and CRM API documentation here.
- https://opentelemetry.io/docs/
- https://playwright.dev/
- https://docs.docker.com/compose/

Employer role references underpinning this project direction, not promises of eligibility or an offer:

- https://scale.com/careers/4694861005
- https://jobs.lever.co/palantir/5168e8fd-fec1-4fea-b7a1-81bdaea65850
- https://job-boards.greenhouse.io/anthropic/jobs/5423029008
- https://www.databricks.com/company/careers/professional-services-operations/forward-deployed-engineer-8921476002

Listings and dependency interfaces change; recheck when needed. The project supports a portfolio discussion of application/integration FDE work. It does not establish years of enterprise experience, regulated-domain expertise or a guaranteed hiring outcome.

## 22. Final handoff from Cursor

When work finishes, return a concise report with:

1. What actually works and how to start it.
2. What was tested, including actual commands and outcomes.
3. Status of offline release, external CRM, live AI, practitioner validation and public deployment separately.
4. Remaining blockers and known defects, without burying them.
5. Where Vinay should begin learning and the exact demo steps.

Leave all relevant implementation context in the project files so another coding agent can continue without this chat's history. Do not mark the entire project verified when an external gate is still pending.
