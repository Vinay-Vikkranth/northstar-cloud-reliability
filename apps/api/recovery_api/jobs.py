import os
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from recovery_api.models import WorkerJob

OWNER = f"{socket.gethostname()}:{os.getpid()}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(db: Session, *, workspace_id, kind: str, payload: dict, delay_seconds: int = 0, max_attempts: int = 5) -> WorkerJob:
    from opentelemetry.propagate import inject
    carrier = {}
    inject(carrier)
    job = WorkerJob(
        workspace_id=workspace_id,
        kind=kind,
        payload=payload,
        state="pending",
        scheduled_at=utcnow() + timedelta(seconds=delay_seconds),
        max_attempts=max_attempts,
        attempts=0,
        trace_context=carrier,
    )
    db.add(job)
    return job


def claim_job(db: Session, owner: str = OWNER, lease_seconds: int | None = None) -> WorkerJob | None:
    now = utcnow()
    job = db.scalar(
        select(WorkerJob)
        .where(WorkerJob.state == "pending", WorkerJob.scheduled_at <= now)
        .order_by(WorkerJob.scheduled_at, WorkerJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        job = db.scalar(
            select(WorkerJob)
            .where(WorkerJob.state == "leased", WorkerJob.lease_expires.is_not(None), WorkerJob.lease_expires < now)
            .order_by(WorkerJob.scheduled_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    if job is None:
        return None
    if job.attempts + 1 > job.max_attempts:
        job.state = "dead"
        job.last_error = "attempt limit reached"
        db.commit()
        return None
    from recovery_api.settings import get_settings

    seconds = lease_seconds if lease_seconds is not None else get_settings().cir_lease_seconds
    job.state = "leased"
    job.lease_owner = owner
    job.lease_expires = now + timedelta(seconds=seconds)
    job.heartbeat_at = now
    job.attempts += 1
    import uuid
    job.lease_token = str(uuid.uuid4())
    db.commit()
    return job


def renew_lease(db: Session, job_id, owner: str, lease_seconds: int | None = None, token: str | None = None) -> bool:
    from recovery_api.settings import get_settings

    seconds = lease_seconds if lease_seconds is not None else get_settings().cir_lease_seconds
    now = utcnow()
    conditions = [WorkerJob.id == job_id, WorkerJob.state == "leased", WorkerJob.lease_owner == owner, WorkerJob.lease_expires > now]
    if token is not None:
        conditions.append(WorkerJob.lease_token == token)
    result = db.execute(update(WorkerJob).where(*conditions).values(lease_expires=now + timedelta(seconds=seconds), heartbeat_at=now))
    db.commit()
    return result.rowcount == 1


def finish_job(db: Session, job: WorkerJob, *, error: str | None = None, owner: str | None = None, token: str | None = None, duration_ms: float | None = None) -> bool:
    current = db.scalar(
        select(WorkerJob)
        .where(WorkerJob.id == job.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None:
        db.rollback()
        return False
    if owner is not None and (current.state != "leased" or current.lease_owner != owner):
        db.rollback()
        return False
    if token is not None and (current.lease_token != token or current.lease_expires is None or current.lease_expires <= utcnow()):
        db.rollback()
        return False
    current.last_duration_ms = duration_ms
    if error:
        current.last_error = error[:500]
        if current.attempts >= current.max_attempts:
            current.state = "dead"
        else:
            current.state = "pending"
            current.scheduled_at = utcnow() + timedelta(seconds=min(30, 2 * current.attempts))
            current.lease_owner = None
            current.lease_expires = None
    else:
        current.state = "done"
        current.last_error = None
        current.finished_at = utcnow()
    db.commit()
    return True


def lease_is_current(db: Session, job_id, owner: str, token: str) -> bool:
    return db.scalar(select(WorkerJob.id).where(WorkerJob.id == job_id, WorkerJob.state == "leased", WorkerJob.lease_owner == owner, WorkerJob.lease_token == token, WorkerJob.lease_expires > utcnow())) is not None


def release_pending(db: Session, *, kind: str, payload_key: str, payload_values: set[str]) -> None:
    """Mark queued jobs done. A job that is already leased still has to stop itself."""
    if not payload_values:
        return
    jobs = db.scalars(select(WorkerJob).where(WorkerJob.kind == kind, WorkerJob.state == "pending")).all()
    for job in jobs:
        if str((job.payload or {}).get(payload_key) or "") in payload_values:
            job.state = "done"
            job.last_error = "cancelled"


def pending_exists(db: Session, workspace_id, kind: str) -> bool:
    row = db.scalar(
        select(WorkerJob.id).where(
            WorkerJob.workspace_id == workspace_id,
            WorkerJob.kind == kind,
            or_(WorkerJob.state == "pending", WorkerJob.state == "leased"),
        )
    )
    return row is not None
