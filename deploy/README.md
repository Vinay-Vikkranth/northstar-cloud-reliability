# Cloud delivery and release operations

This directory implements a single Linux VM deployment recipe. No cloud account, VM, paid service, domain or public deployment was created during implementation. The source snapshot/rollback tests are executable locally without Docker. Actual container startup and provider validation have separate evidence statuses in the main README.

## Topology

Only Caddy publishes ports 80/443. It obtains HTTPS certificates for an operator-controlled DNS hostname. The API serves the compiled React UI and authenticated API on one origin. PostgreSQL, the worker and both synthetic connector services have no published ports and sit on an internal Docker network. The public proxy refuses internal metrics, raw health paths, docs and simulator webhook ingress. `/healthz` exposes the application's readiness check. Authenticated operations views remain available through the regular API.

PostgreSQL data, runtime/audit records, generated demo credentials and certificate state use persistent named volumes. Recreating containers does not reset those volumes. `seed_once.py` seeds an empty installation exactly once; it refuses to reset an existing database without its expected marker. Real HubSpot integration and model APIs are disabled in this production recipe. It is a deterministic synthetic portfolio environment, not a customer deployment.

The convenience combined-role and cross-workspace demo users are disabled by the cloud initializer. Use separate operator and approver logins for the normal workflow; keep the administrator credential private for account management. Database-owner credentials are supplied only to the migration job, and each simulator receives only its own database and connector secrets.

## Prepare without launching cloud resources

On the chosen Linux host, install Docker Engine and Compose from Docker's official documentation. Choose a VM, its region and spending limit before provisioning. This recipe can incur VM, disk, backup, DNS/domain, public IPv4 and network egress costs. No price or free-tier guarantee is made. Shut down or remove compute using the selected provider when finished; do not delete database volumes while you need the records.

Generate private credentials once, replacing the example hostname and email with operator-controlled values:

```bash
python scripts/release.py init-env --domain northstar.example.com --email operator@example.com
```

Secrets are written to ignored `var/cloud/production.env`, never printed. The command refuses to overwrite an existing file. Protect and back up that file separately; generated role passwords are needed to reconnect to persistent databases. Do not paste a resolved `docker compose config` into public logs because it can contain secrets. Use `config --quiet` for syntax validation.

Create a content-verified release:

```bash
python scripts/release.py stage --version 2026.11.08.1
# Copy the printed release_id into the next command.
python scripts/release.py verify --id RELEASE_ID
python scripts/release.py activate --id RELEASE_ID
python scripts/release.py status
```

Without `--deploy`, activation changes only the atomic artifact-store pointer. It does not start, stop or publish an application. The store under `var/releases` excludes credentials, logs, runtime databases, dependency folders and generated frontend builds. Each snapshot carries file SHA-256 values and a content-derived identity. Verification rejects modified, missing and unmanifested files. These are immutable *source snapshots*, not a claim that floating upstream base-image tags reproduce byte-identical container images.

## Deploy only after infrastructure approval

Point DNS at the chosen VM, allow inbound 80/443 and restrict SSH to the operator. No database or connector ports should be opened in the cloud firewall. Copy the private environment through an appropriate private channel and keep it outside release snapshots.

```bash
python scripts/release.py activate --id RELEASE_ID --deploy --env-file var/cloud/production.env
```

This opt-in command actually runs Docker Compose against the verified snapshot, builds its image, starts services and probes API readiness inside the private container network. Only a successful readiness check records `deployed_ready`. TLS/DNS and an authenticated full workflow must also be checked externally before reporting a successful public deployment. The tool does not purchase or provision infrastructure.

The container runs as UID10001, with a read-only root filesystem and a writable private runtime volume. Database roles have distinct injected passwords. The API requires secure cookies, explicit HTTPS origins and long connector/webhook secrets. No development database initializer or shared default password is used in this recipe.

## Production container smoke test

`deploy/smoke.py` runs inside an isolated production API container after the API, seed job and worker are ready. It checks the nonroot UID, compiled web UI, secure session cookies, disabled convenience accounts, separate operator and approver permissions, a real worker-backed repair, fresh verification and exactly one destination write. It also confirms the synthetic CRM source revision did not change. It never resets data. A repeat run verifies the already resolved fixture and labels that distinction in its report.

Because TLS terminates at Caddy, the test explicitly forwards the Secure cookies over the private HTTP hop as a proxy would; it does not disable Secure cookies or alter production settings. Credentials are read only inside the container from its private runtime volume and are never printed. The summary is stored at `/app/var/production-smoke.json`. Container smoke evidence does not prove public DNS, TLS certificate issuance or provider deployment.

```bash
# Run only against the isolated CI project or approved synthetic host.
docker compose --env-file var/cloud/production.env -f compose.production.yaml exec -T api python deploy/smoke.py
```

## Rollback and failure handling

```bash
# Real local artifact rollback, without touching any cloud process:
python scripts/release.py rollback
# Real container rollback on an already approved host:
python scripts/release.py rollback --deploy --env-file var/cloud/production.env
```

Source checksum validation happens before either pointer changes or container commands. A mutation lock serializes releases, and stale rollback state is rejected. Repeating activation of the current release preserves its previous release. On a failed deployment, the tool attempts to restart the prior deployed snapshot and probes it. If recovery also fails, status explicitly records failure rather than claiming health. First-deployment failures may leave partial containers and persistent volumes for inspection; no automatic cleanup deletes data.

Rollback restores application code; it never downgrades a database. Changed migration files block deployment until backward compatibility has been reviewed and `--allow-schema-change` is intentionally supplied. Use expand-compatible migrations, back up the database before releases, and exercise restore in an isolated database. A source rollback cannot undo external effects or restore deleted data. Existing support receipts and approval records remain in PostgreSQL, preventing reset of the duplicate-effect history.

If a process is killed while holding `var/releases/.release.lock`, inspect the recorded PID and confirm no release is running before removing that one stale lock file. Never remove it to bypass an active deployment.

## Private inspection and costs

```bash
docker compose --env-file var/cloud/production.env -f var/releases/RELEASE_ID/compose.production.yaml ps
docker compose --env-file var/cloud/production.env -f var/releases/RELEASE_ID/compose.production.yaml logs --tail 100 api worker
```

Application logs and traces redact sensitive fields, but runtime credentials and raw environment values remain private. Use authenticated operations pages for the workspace-specific SLO and queue metrics. The application objective is a demo SLO, not a contracted SLA. Backend execution time and approval wait are different measurements. Financial estimates require the selected provider's actual rates and runtime; the implementation does not fabricate hosting costs or business savings.

Official configuration references: [Docker Compose services](https://docs.docker.com/reference/compose-file/services/), [Docker installation](https://docs.docker.com/engine/install/), [Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy), and [request size restrictions](https://caddyserver.com/docs/caddyfile/directives/request_body).
