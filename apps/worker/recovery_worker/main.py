"""Claim jobs, run them, and stop only this process on request."""

from __future__ import annotations

import logging
import threading
import time
import argparse
import hashlib
from opentelemetry.propagate import extract

from recovery_api.db import session_factory
from recovery_api.execute import execute_intent
from recovery_api.investigate import run_investigation
from recovery_api.jobs import OWNER, claim_job, finish_job, renew_lease, lease_is_current
from recovery_api.models import RepairIntent
from recovery_api.syncing import reconcile_workspace
from recovery_api.telemetry import configure_logging, tracer
from recovery_core.redact import redact_text

log = logging.getLogger("recovery.worker")


def _run(job, execution_owner: str = OWNER, lease_guard=None) -> None:
    db = session_factory()()
    try:
        if job.kind == "investigate":
            run_investigation(job.payload["investigation_id"])
        elif job.kind == "execute_repair":
            execute_intent(db, job.payload["intent_id"], execution_owner, lease_guard=lease_guard)
        elif job.kind == "reconcile":
            reconcile_workspace(db, job.payload["workspace_id"])
        elif job.kind == "sync_retry":
            from recovery_api.models import SyncEvent
            from sqlalchemy import select

            event = db.scalar(select(SyncEvent).where(SyncEvent.external_event_id == job.payload["event_id"]))
            if event is not None:
                log.info("sync retry recorded for %s; operator can reconcile", event.external_event_id)
        elif job.kind == "recover_repairs":
            rows = db.query(RepairIntent).filter(
                RepairIntent.workspace_id == job.workspace_id,
                RepairIntent.state.in_(["DISPATCHING", "OUTCOME_UNKNOWN", "SUCCEEDED", "RECONCILING"])
            ).all()
            ids = [row.id for row in rows]
            db.commit()
            for intent_id in ids:
                session = session_factory()()
                try:
                    execute_intent(session, intent_id, execution_owner, lease_guard=lease_guard)
                finally:
                    session.close()
        else:
            raise RuntimeError(f"unknown job {job.kind}")
    finally:
        db.close()


def process_available_jobs(limit: int = 20) -> int:
    processed = 0
    while processed < limit:
        db = session_factory()()
        try:
            job = claim_job(db, OWNER)
        finally:
            db.close()
        if job is None:
            break
        db = session_factory()()
        try:
            current = db.get(type(job), job.id)
            stop = threading.Event()
            lost = threading.Event()
            token = current.lease_token
            job_id = current.id
            from recovery_api.settings import get_settings
            heartbeat_seconds = max(.05, min(10, get_settings().cir_lease_seconds / 3))

            def _beat() -> None:
                while not stop.wait(heartbeat_seconds):
                    beat = session_factory()()
                    try:
                        if not renew_lease(beat, job_id, OWNER, token=token):
                            lost.set()
                            return
                    except Exception:
                        lost.set()
                        log.exception("lease heartbeat failed; further dispatch is fenced")
                        return
                    finally:
                        beat.close()

            beater = threading.Thread(target=_beat, daemon=True)
            beater.start()
            try:
                start = time.perf_counter()
                with tracer().start_as_current_span("worker." + current.kind, context=extract(current.trace_context or {})) as span:
                    span.set_attribute("job.id", str(current.id))
                    span.set_attribute("job.attempt", current.attempts)
                    span.set_attribute("workspace_id", str(current.workspace_id))
                    log.info("job started id=%s kind=%s attempt=%s", current.id, current.kind, current.attempts)
                    execution_owner = hashlib.sha256(f"{OWNER}:{token}".encode()).hexdigest()
                    def lease_guard():
                        if lost.is_set():return False
                        check = session_factory()()
                        try:
                            return lease_is_current(check, job_id, OWNER, token)
                        except Exception:
                            lost.set()
                            return False
                        finally:
                            check.close()
                    _run(current, execution_owner, lease_guard=lease_guard)
                    done = finish_job(db, current, owner=OWNER, token=token, duration_ms=(time.perf_counter()-start)*1000)
                    log.info("job finished id=%s committed=%s lease_lost=%s", current.id, done, lost.is_set())
            except Exception as exc:
                db.rollback()
                current = db.get(type(job), job.id)
                finish_job(db, current, error=redact_text(str(exc)), owner=OWNER, token=token, duration_ms=(time.perf_counter()-start)*1000)
                log.exception("job failed")
            finally:
                stop.set()
                beater.join(timeout=1)
        finally:
            db.close()
        processed += 1
    return processed


def main() -> None:
    from recovery_api.build_info import BUILD_ID

    configure_logging()
    parser = argparse.ArgumentParser(description="Run the durable Northstar worker")
    parser.add_argument("--once", action="store_true", help="Drain available jobs then exit, useful for release/crash proofs")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    log.info("worker started build=%s", BUILD_ID)
    if args.once:
        print(f"processed={process_available_jobs(args.limit)}")
        return
    while True:
        process_available_jobs()
        db = session_factory()()
        try:
            from recovery_api.jobs import enqueue, pending_exists
            from recovery_api.models import Workspace
            from sqlalchemy import select

            for workspace in db.scalars(select(Workspace).where(Workspace.synthetic.is_(True))).all():
                if not pending_exists(db, workspace.id, "recover_repairs"):
                    enqueue(db, workspace_id=workspace.id, kind="recover_repairs", payload={}, delay_seconds=10)
            db.commit()
        finally:
            db.close()
        time.sleep(1)


if __name__ == "__main__":
    main()
