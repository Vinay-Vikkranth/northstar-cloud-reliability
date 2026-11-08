"""Workspace-scoped operational evidence. Payloads and secrets are never returned."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from recovery_api.db import get_session
from recovery_api.models import WorkerJob, Membership, RepairIntent
from recovery_api.routes import require
from recovery_api.records import audit

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])

def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (position-lo), 3)

def summarize(jobs: list[WorkerJob], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    maintenance = sum(getattr(job,"kind",None)=="recover_repairs" for job in jobs)
    jobs = [job for job in jobs if getattr(job,"kind",None)!="recover_repairs"]
    counts = Counter(job.state for job in jobs)
    durations = [float(job.last_duration_ms) for job in jobs if job.state == "done" and job.last_duration_ms is not None]
    terminal = counts["done"] + counts["dead"]
    completion = counts["done"] / terminal if terminal else None
    expired = sum(job.state == "leased" and job.lease_expires is not None and job.lease_expires < now for job in jobs)
    alerts = []
    if counts["dead"]:
        alerts.append({"code":"dead_letter", "severity":"warning", "message":f"{counts['dead']} job(s) exhausted retry budgets; inspect before any explicit replay."})
    if expired:
        alerts.append({"code":"expired_lease", "severity":"warning", "message":f"{expired} lease(s) can be reclaimed by a worker."})
    p95 = percentile(durations, .95)
    measured = terminal >= 10 and len(durations) >= 10
    if measured and (completion < .99 or p95 > 5000):
        alerts.append({"code":"slo_breach", "severity":"critical", "message":"Observed terminal job success or p95 duration is outside the demo objective."})
    return {"as_of":now.isoformat(), "scope":"recorded non-maintenance jobs in the selected workspace", "maintenance_jobs_excluded":maintenance, "counts":dict(counts),
        "terminal_jobs":terminal, "duration_samples":len(durations), "completion_rate":completion,
        "p50_ms":percentile(durations,.5), "p95_ms":p95, "expired_leases":expired, "alerts":alerts,
        "slo":{"success_target":.99,"p95_target_ms":5000,"minimum_samples":10,"status":"insufficient_data" if not measured else ("breached" if any(a['code']=='slo_breach' for a in alerts) else "within_objective")},
        "limits":"Portfolio demo objective, not a production SLA. Duration is the last successful execution per job, excluding approval wait. Terminal success is done / (done + dead), not per-attempt availability."}

@router.get("")
def overview(member: Membership = Depends(require("viewer")), db: Session = Depends(get_session)):
    jobs = list(db.scalars(select(WorkerJob).where(WorkerJob.workspace_id == member.workspace_id)).all())
    summary = summarize(jobs)
    recent = sorted(jobs, key=lambda job:job.created_at, reverse=True)[:40]
    summary["jobs"] = [{"id":str(job.id),"kind":job.kind,"state":job.state,"attempts":job.attempts,"max_attempts":job.max_attempts,
        "scheduled_at":job.scheduled_at.isoformat(), "lease_expires":job.lease_expires.isoformat() if job.lease_expires else None,
        "duration_ms":job.last_duration_ms,"traceparent":(job.trace_context or {}).get("traceparent"),"finished_at":job.finished_at.isoformat() if job.finished_at else None} for job in recent]
    summary["repair_states"] = dict(Counter(db.scalars(select(RepairIntent.state).where(RepairIntent.workspace_id == member.workspace_id)).all()))
    return summary

class ReplayIn(BaseModel):
    reason: str = Field(min_length=10, max_length=300)

@router.post("/jobs/{job_id}/replay")
def replay(job_id: UUID, payload: ReplayIn, member: Membership = Depends(require("administrator")), db: Session = Depends(get_session)):
    job = db.scalar(select(WorkerJob).where(WorkerJob.id == job_id, WorkerJob.workspace_id == member.workspace_id).with_for_update())
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.state != "dead":
        raise HTTPException(409, "Only a dead-letter job can be replayed")
    if job.max_attempts >= 20:
        raise HTTPException(409, "Manual replay budget exhausted; investigate the root cause")
    job.max_attempts = min(20, job.max_attempts + 5)
    job.state = "pending"
    job.scheduled_at = datetime.now(timezone.utc)
    job.lease_owner = None
    job.lease_token = None
    job.lease_expires = None
    audit(db, actor_id=member.user_id, workspace_id=member.workspace_id, action="job_replay_requested", resource_type="worker_job", resource_id=str(job.id), metadata={"reason":payload.reason,"attempts_preserved":job.attempts})
    db.commit()
    return {"id":str(job.id),"state":job.state,"attempts":job.attempts,"max_attempts":job.max_attempts,"note":"Repair intent dispatch budget and original approval remain unchanged."}
