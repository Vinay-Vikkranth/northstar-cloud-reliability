# November 8 validation

The integrated suite passed109 tests, zero failures and three inherited deprecation warnings in63.35seconds. It includes the70 original tests, new pre-dispatch fencing, worker-log correlation, replay, production two-person checks, benchmark boundary checks and12 release/configuration tests. Tests used fresh PostgreSQL55438 and separate test services28711/28721. Locked frontend installation reported zero npm vulnerabilities and passed TypeScript/Vite build. `evidence/validation.json` records the exact commands and source preservation check; all125 inherited source file hashes still match the read-only originals and runtime packages match requirements.lock.

Fresh synthetic startup, API/UI28601, CRM28611 and support28621 were exercised. Original source8100/55432 services were not stopped, reset or edited. Source `.env` and HubSpot records were not copied.

Tests use real PostgreSQL and HTTP simulators, terminate an owned child after its durable claim, verify a fresh recovery token, and retain inherited crash-after-write receipt proofs. They do not establish cross-region failover, production capacity or customer workflows.

```text
python -B scripts/native.py test
cd apps/web
npm ci
npm run build
```

The machine has no Docker runtime. GitHub Actions is configured to boot the development composition and execute a synthetic workflow benchmark, beyond health alone; its actual completed run is authoritative for container validation. The production composition is prepared but no cloud VM or paid service has been activated.

Inherited warnings remain visible: FastAPI `on_event` startup is deprecated in favor of lifespan; LangGraph warns about a future serializer default. They did not fail the measured suite and are not suppressed to imply newer framework conformance.
