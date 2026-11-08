HISTORICAL SOURCE DOCUMENT: copied from the earlier local Northstar project. Its former machine settings, live-account setup and evidence do not apply to a fresh clone of this release. Use README.md and docs/VALIDATION_2026-11-08.md for current setup, measured results and limitations. No old private account identifiers or credentials are supplied.

# Operations

## Ports

| Process | Port |
| --- | --- |
| Web | 8100 |
| API | 8101 |
| CRM simulator | 8111 |
| Support service | 8121 |
| PostgreSQL | 55432 |

Logs: `var/logs/`. Process ids started by the scripts: `var/pids.json`.

## Windows

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
```

`start.ps1` checks the four application ports, starts the bundled database if this project's data directory owns 55432, migrates, grants, creates checkpoint tables, starts the simulators, seeds, then starts the API, worker, and web app.

## Unix

`scripts/start.sh` and `scripts/stop.sh` follow the same order. They expect `.venv/bin/python`. They were not executed on this Windows machine.

## Docker Compose

`compose.yaml` builds the API image, the two simulators from that image, the worker, the web image, and Postgres 16. On a machine with Docker:

```bash
docker compose up --build
```

That command was not run here. After the first boot, run `python scripts/seed.py` with the Compose database URLs if you want the demo users. Do not point demo reset at a HubSpot token.

## Backup

```powershell
.\.venv\Scripts\python.exe scripts\backup_restore.py
```

This writes `var/cir-backup.sql`, restores it into `cir_restore_check`, counts `workspaces`, and drops that database. The 2026-11-02 run printed `restore_workspaces=42`. Receipts in the support database are the destination's idempotency record. Keep them at least as long as a repair can be retried. This project does not delete them from the demo service except when an admin resets the simulator.

## Pending on purpose

- HubSpot and live-model smokes are configured on this machine as of November 4; fresh clones exit 2 until configured. Run `scripts/check_setup.py` to check availability without inference.
- Import an existing live evaluation for the UI using `python scripts/import_live_evaluation.py evaluations/results/live.json`. Older reports without model metadata require an explicit `--model`; importing does not rerun or improve the scores.
- `sync_retry` asks the operator to reconcile. It does not write.
- Cloud hosting is not provisioned. A host needs a persistent worker and PostgreSQL. No billable service was created.
