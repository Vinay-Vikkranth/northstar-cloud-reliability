"""Fencing, durable approval, process termination and scoped operations proofs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import timedelta
import uuid
import pytest
from sqlalchemy import select, update
from recovery_api.db import session_factory
from recovery_api.jobs import claim_job, enqueue, finish_job, renew_lease
from recovery_api.models import WorkerJob, RepairIntent
from recovery_api.records import utcnow
from recovery_api.execute import execute_intent
from recovery_api.telemetry import configure_logging
from recovery_worker.main import process_available_jobs
from test_review_regressions import _prepare,_approve,_account,_quiesce
from test_safety import _crash_env

pytestmark=pytest.mark.integration
ROOT=Path(__file__).resolve().parents[2]

def test_old_claim_cannot_finish_or_renew_after_reclaim_same_owner(services):
    ctx=_prepare("fence")
    db=session_factory()()
    try:
        queued=enqueue(db,workspace_id=uuid.UUID(ctx["workspace_id"]),kind="reconcile",payload={"workspace_id":ctx["workspace_id"]})
        db.commit();first=claim_job(db,"same-owner");token=first.lease_token
        db.execute(update(WorkerJob).where(WorkerJob.id==first.id).values(lease_expires=utcnow()-timedelta(seconds=2)));db.commit()
        current=claim_job(db,"same-owner");assert current.lease_token!=token
        assert not finish_job(db,current,owner="same-owner",token=token)
        assert not renew_lease(db,current.id,"same-owner",token=token)
        assert finish_job(db,current,owner="same-owner",token=current.lease_token,duration_ms=2)
    finally:db.close()

def test_expired_lease_cannot_be_resurrected_by_old_heartbeat(services):
    ctx=_prepare("expired")
    db=session_factory()()
    try:
        enqueue(db,workspace_id=uuid.UUID(ctx["workspace_id"]),kind="reconcile",payload={});db.commit()
        first=claim_job(db,"old",lease_seconds=0)
        assert not renew_lease(db,first.id,"old",token=first.lease_token)
        assert not finish_job(db,first,owner="old",token=first.lease_token)
    finally:db.close()

def test_lost_queue_lease_blocks_starting_destination_side_effect(services):
    ctx=_prepare("lost-before-dispatch");_approve(ctx)
    db=session_factory()()
    try:
        intent=db.scalar(select(RepairIntent).where(RepairIntent.workspace_id==uuid.UUID(ctx["workspace_id"])))
        with pytest.raises(RuntimeError,match="lease lost"):
            execute_intent(db,intent.id,"stale",lease_guard=lambda:False)
        assert _account()["write_count"]==0
    finally:db.close()

def test_terminated_process_claim_recovers_after_lease_expiry(services,tmp_path):
    ctx=_prepare("terminate")
    db=session_factory()()
    queued=enqueue(db,workspace_id=uuid.UUID(ctx["workspace_id"]),kind="reconcile",payload={"workspace_id":ctx["workspace_id"]});db.commit();job_id=queued.id;db.close()
    marker=tmp_path/"claimed.json"
    code="""import json,time,sys
from recovery_api.db import session_factory
from recovery_api.jobs import claim_job
db=session_factory()();job=claim_job(db,'child-to-terminate',lease_seconds=1)
open(sys.argv[1],'w').write(json.dumps({'id':str(job.id),'token':job.lease_token}))
time.sleep(60)
"""
    env=_crash_env("");env.pop("CIR_CRASH_POINT",None)
    child=subprocess.Popen([sys.executable,"-B","-c",code,str(marker)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+15
        while not marker.exists() and time.monotonic()<deadline:
            if child.poll() is not None:pytest.fail(child.stderr.read().decode())
            time.sleep(.05)
        assert marker.exists()
        original=json.loads(marker.read_text());assert original["id"]==str(job_id)
        child.terminate();child.wait(timeout=5)
        time.sleep(1.1)
        db=session_factory()()
        try:
            resumed=claim_job(db,"restarted")
            assert resumed.id==job_id and resumed.attempts==2
            assert resumed.lease_token!=original["token"]
            assert finish_job(db,resumed,owner="restarted",token=resumed.lease_token)
        finally:db.close()
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)

def test_operations_are_workspace_scoped_and_payload_free(services):
    ctx=_prepare("ops")
    data=ctx["operator_client"].get("/api/v1/operations")
    assert data.status_code==200,data.text
    assert data.json()["jobs"]
    assert all("payload" not in row and "last_error" not in row for row in data.json()["jobs"])
    ctx["operator_client"].headers["X-Workspace-Id"]=str(uuid.uuid4())
    assert ctx["operator_client"].get("/api/v1/operations").status_code==404

def test_http_trace_is_persisted_through_durable_job(services):
    configure_logging();ctx=_prepare("trace")
    carrier="00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
    response=ctx["operator_client"].post(f"/api/v1/incidents/{ctx['incident_id']}/investigate",headers={"traceparent":carrier})
    assert response.status_code==200
    assert response.headers["x-trace-id"]=="1234567890abcdef1234567890abcdef"
    db=session_factory()()
    try:
        job=db.scalar(select(WorkerJob).where(WorkerJob.workspace_id==uuid.UUID(ctx["workspace_id"]),WorkerJob.state=="pending"))
        assert job.trace_context["traceparent"].split("-")[1]=="1234567890abcdef1234567890abcdef"
    finally:db.close()
    assert process_available_jobs() >= 1
    from opentelemetry import trace
    from recovery_api.settings import get_settings
    trace.get_tracer_provider().force_flush()
    logs=Path(get_settings().cir_log_dir)/"recovery.log"
    rows=[json.loads(line) for line in logs.read_text(encoding="utf-8").splitlines()]
    assert any(row.get("trace_id")=="1234567890abcdef1234567890abcdef" and "job finished" in row.get("message","") for row in rows)

def test_lease_lost_after_intent_commit_blocks_new_http_write(services):
    ctx=_prepare("late-fence");_approve(ctx)
    db=session_factory()();calls=[]
    def guard():
        calls.append(1)
        return len(calls)==1
    try:
        intent=db.scalar(select(RepairIntent).where(RepairIntent.workspace_id==uuid.UUID(ctx["workspace_id"])))
        with pytest.raises(RuntimeError,match="before destination dispatch"):
            execute_intent(db,intent.id,"stale-after-reads",lease_guard=guard)
        assert len(calls)==2 and _account()["write_count"]==0
    finally:db.close()

def test_dead_letter_replay_requires_admin_preserves_attempts_and_audits(services):
    ctx=_prepare("replay");_quiesce()
    db=session_factory()()
    try:
        job=enqueue(db,workspace_id=uuid.UUID(ctx["workspace_id"]),kind="reconcile",payload={"workspace_id":ctx["workspace_id"]});job.state="dead";job.attempts=5;job.max_attempts=5;db.commit();job_id=job.id
    finally:db.close()

    path=f"/api/v1/operations/jobs/{job_id}/replay"
    assert ctx["approver_client"].post(path,json={"reason":"Investigated synthetic connector failure"}).status_code==403
    response=ctx["operator_client"].post(path,json={"reason":"Investigated synthetic connector failure"})
    assert response.status_code==200,response.text
    assert response.json()["attempts"]==5 and response.json()["max_attempts"]==10
    assert ctx["operator_client"].post(path,json={"reason":"Accidental repeated replay should fail"}).status_code==409
    from recovery_api.models import AuditEvent
    db=session_factory()()
    try:assert db.scalar(select(AuditEvent).where(AuditEvent.resource_id==str(job_id),AuditEvent.action=="job_replay_requested")) is not None
    finally:db.close()

def test_production_rejects_self_approval_even_for_an_administrator(services,monkeypatch):
    ctx=_prepare("two-person")
    from recovery_api.settings import get_settings
    monkeypatch.setattr(get_settings(),"cir_environment","production")
    path=f"/api/v1/proposals/{ctx['proposal']['id']}/approve"
    payload={"proposal_hash":ctx["proposal"]["proposal_hash"],"reason":"Independently reviewed both system revisions"}
    rejected=ctx["operator_client"].post(path,json=payload)
    assert rejected.status_code==403 and rejected.json()["error"]["code"]=="separate_approver"
    assert ctx["approver_client"].post(path,json=payload).status_code==200
