import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from recovery_api.db import get_session
from recovery_api.demo import apply_scenario, assert_demo_workspace
from recovery_api.jobs import enqueue, release_pending
from recovery_api.models import (
    Approval,
    AuditEvent,
    ConnectorConfig,
    CustomerLink,
    Diagnosis,
    EvaluationReport,
    Evidence,
    Incident,
    InvestigationRun,
    Membership,
    Proposal,
    RepairAttempt,
    RepairIntent,
    User,
    Verification,
    Workspace,
)
from recovery_api.records import audit, utcnow
from recovery_api.security import (
    create_session,
    has_role,
    hash_password,
    membership_for,
    record_login,
    revoke_token,
    too_many_failures,
    user_from_token,
    verify_password,
)
from recovery_api.settings import get_settings
from recovery_api.syncing import ingest_event, reconcile_workspace
from recovery_core.hashing import canonical_hash
from recovery_core.proposals import hash_proposal_fields, isoformat_utc

router = APIRouter(prefix="/api/v1")
ALLOWED_PROVIDERS = {"crm_simulator", "support_service", "hubspot"}


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=10, max_length=200)


class UserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=10, max_length=200)
    workspace_id: UUID
    roles: list[str] = Field(min_length=1)


class IncidentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=4000)
    severity: str = "medium"
    customer_link_id: UUID | None = None


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_hash: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=3, max_length=1000)


class ConnectorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=80)
    provider: str
    plan_property: str | None = None
    mapping_version: str = "v1"


class ScenarioIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: str


def _error(status: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def current_user(request: Request, db: Session = Depends(get_session)) -> User:
    user = user_from_token(db, request.cookies.get("cir_session"))
    if user is None:
        _error(401, "unauthenticated", "Sign in is required")
    return user


def current_membership(
    x_workspace_id: UUID = Header(),
    user: User = Depends(current_user),
    db: Session = Depends(get_session),
) -> Membership:
    row = membership_for(db, user, x_workspace_id)
    if row is None:
        _error(404, "not_found", "Workspace not found")
    return row


def require(*roles: str):
    def dependency(member: Membership = Depends(current_membership)) -> Membership:
        if not has_role(member, *roles):
            _error(403, "forbidden", "You do not have permission for this action")
        return member

    return dependency


def _cookies(response: Response, token: str, csrf: str) -> None:
    settings = get_settings()
    common = {
        "secure": settings.cir_cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": settings.cir_session_hours * 3600,
    }
    response.set_cookie("cir_session", token, httponly=True, **common)
    response.set_cookie("cir_csrf", csrf, httponly=False, **common)


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_session)) -> dict:
    key = request.client.host if request.client else "unknown"
    if too_many_failures(db, key):
        db.commit()
        _error(429, "rate_limited", "Too many sign-in attempts. Wait and try again.")
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or user.disabled or not verify_password(user.password_hash, payload.password):
        record_login(db, key, False)
        db.commit()
        _error(401, "invalid_login", "Username or password is incorrect")
    record_login(db, key, True)
    token, csrf = create_session(db, user)
    audit(db, action="login", resource_type="user", resource_id=str(user.id), actor_id=user.id)
    db.commit()
    _cookies(response, token, csrf)
    memberships = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    return {
        "username": user.username,
        "csrf_token": csrf,
        "workspaces": [_workspace_brief(db, row) for row in memberships],
    }


@router.post("/auth/logout")
def logout(request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_session)) -> dict:
    revoke_token(db, request.cookies.get("cir_session"))
    audit(db, action="logout", resource_type="user", resource_id=str(user.id), actor_id=user.id)
    db.commit()
    response.delete_cookie("cir_session", path="/")
    response.delete_cookie("cir_csrf", path="/")
    return {"signed_out": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_session)) -> dict:
    memberships = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    return {"username": user.username, "workspaces": [_workspace_brief(db, row) for row in memberships]}


def _workspace_brief(db: Session, membership: Membership) -> dict:
    workspace = db.get(Workspace, membership.workspace_id)
    return {
        "id": str(workspace.id),
        "name": workspace.name,
        "slug": workspace.slug,
        "synthetic": workspace.synthetic,
        "roles": membership.roles,
    }


@router.get("/meta")
def meta(member: Membership = Depends(current_membership), db: Session = Depends(get_session)) -> dict:
    settings = get_settings()
    workspace = db.get(Workspace, member.workspace_id)
    source = db.scalar(
        select(ConnectorConfig).where(
            ConnectorConfig.workspace_id == workspace.id,
            ConnectorConfig.provider.in_(["crm_simulator", "hubspot"]),
        )
    )
    external = bool(source and source.provider == "hubspot")
    live = settings.cir_investigator_mode == "live"
    return {
        "source_mode": "external_crm" if external else "synthetic_crm",
        "synthetic_demo": workspace.synthetic and not external,
        "investigator_mode": "live" if live else "deterministic",
        "provider": "openai_compatible" if live else "rules",
        "model": settings.cir_llm_model or None,
        "banner": "HubSpot read-only source, local demo support destination" if external else "Synthetic demo",
    }


@router.post("/users")
def create_user(payload: UserIn, member: Membership = Depends(require("administrator")), db: Session = Depends(get_session)) -> dict:
    if any(role not in {"viewer", "operator", "approver", "administrator"} for role in payload.roles):
        _error(422, "invalid_role", "Unknown role")
    actor = db.get(User, member.user_id)
    target = membership_for(db, actor, payload.workspace_id)
    if target is None or not has_role(target, "administrator"):
        _error(404, "not_found", "Workspace not found")
    if db.scalar(select(User).where(User.username == payload.username)):
        _error(409, "conflict", "Username already exists")
    user = User(username=payload.username, password_hash=hash_password(payload.password), created_at=utcnow())
    db.add(user)
    db.flush()
    db.add(Membership(user_id=user.id, workspace_id=payload.workspace_id, roles=payload.roles, created_at=utcnow()))
    audit(db, actor_id=member.user_id, workspace_id=payload.workspace_id, action="user_created", resource_type="user", resource_id=str(user.id), metadata={"roles": payload.roles})
    db.commit()
    return {"id": str(user.id), "username": user.username}


@router.get("/incidents")
def list_incidents(
    member: Membership = Depends(require("viewer")),
    db: Session = Depends(get_session),
    state: str | None = None,
    q: str | None = None,
    limit: int = 50,
) -> dict:
    limit = min(max(limit, 1), 100)
    stmt = select(Incident).where(Incident.workspace_id == member.workspace_id)
    if state:
        stmt = stmt.where(Incident.state == state)
    if q:
        like = f"%{q[:80]}%"
        stmt = stmt.where(or_(Incident.title.ilike(like), Incident.description.ilike(like)))
    rows = db.scalars(stmt.order_by(Incident.created_at.desc()).limit(limit)).all()
    counts = dict(
        db.execute(
            select(Incident.state, func.count())
            .where(Incident.workspace_id == member.workspace_id)
            .group_by(Incident.state)
        ).all()
    )
    return {"items": [_incident_brief(db, row) for row in rows], "counts": counts}


@router.post("/incidents")
def create_incident(payload: IncidentIn, member: Membership = Depends(require("operator")), db: Session = Depends(get_session)) -> dict:
    if payload.severity not in {"low", "medium", "high"}:
        _error(422, "invalid_severity", "Severity must be low, medium, or high")
    link = None
    if payload.customer_link_id:
        link = db.scalar(
            select(CustomerLink).where(
                CustomerLink.id == payload.customer_link_id,
                CustomerLink.workspace_id == member.workspace_id,
            )
        )
        if link is None:
            _error(404, "not_found", "Customer link not found")
    incident = Incident(
        workspace_id=member.workspace_id,
        customer_link_id=link.id if link else None,
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        state="OPEN",
        owner_user_id=member.user_id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(incident)
    db.flush()
    audit(db, actor_id=member.user_id, workspace_id=member.workspace_id, action="incident_created", resource_type="incident", resource_id=str(incident.id))
    db.commit()
    return _incident_detail(db, incident)


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: UUID, member: Membership = Depends(require("viewer")), db: Session = Depends(get_session)) -> dict:
    incident = _incident(db, member, incident_id)
    return _incident_detail(db, incident)


@router.post("/incidents/{incident_id}/investigate")
def investigate(incident_id: UUID, member: Membership = Depends(require("operator")), db: Session = Depends(get_session)) -> dict:
    incident = _incident(db, member, incident_id)
    if incident.state in {"EXECUTING", "VERIFYING"}:
        _error(409, "busy", "A repair is already in progress")
    settings = get_settings()
    mode = settings.cir_investigator_mode if settings.cir_investigator_mode == "live" else "deterministic"
    run = InvestigationRun(
        workspace_id=member.workspace_id,
        incident_id=incident.id,
        mode=mode,
        provider="openai_compatible" if mode == "live" else "rules",
        model=settings.cir_llm_model or None,
        workflow_version="inv-2026.11.02",
        state="RUNNING",
        budget={
            "max_tool_calls": settings.cir_max_tool_calls,
            "max_tokens": settings.cir_max_tokens,
            "max_elapsed_seconds": settings.cir_max_elapsed_seconds,
            **(settings.cost_controls() if mode == "live" else {"max_estimated_cost_usd": 0, "cost_policy": "not_applicable"}),
        },
        usage={
            "cost_usd": 0 if mode == "deterministic" else None,
            "cost_known": mode == "deterministic",
            "cost_accounting": "not_applicable" if mode == "deterministic" else settings.cost_controls()["cost_policy"],
            "cost_note": "No model call." if mode == "deterministic" else "No provider response yet.",
            "tokens": 0,
        },
        tool_trace=[],
        summary="",
        started_at=utcnow(),
    )
    db.add(run)
    db.flush()
    older = db.scalars(
        select(InvestigationRun).where(
            InvestigationRun.incident_id == incident.id,
            InvestigationRun.id != run.id,
            InvestigationRun.state == "RUNNING",
        )
    ).all()
    for previous in older:
        previous.state = "CANCELLED"
        previous.finished_at = utcnow()
    release_pending(db, kind="investigate", payload_key="investigation_id", payload_values={str(previous.id) for previous in older})
    open_proposals = db.scalars(
        select(Proposal).where(Proposal.incident_id == incident.id, Proposal.workspace_id == member.workspace_id, Proposal.decision_state == "open")
    ).all()
    for previous in open_proposals:
        previous.decision_state = "superseded"
    incident.state = "INVESTIGATING"
    incident.active_investigation_id = run.id
    incident.updated_at = utcnow()
    enqueue(db, workspace_id=member.workspace_id, kind="investigate", payload={"investigation_id": str(run.id), "actor_id": str(member.user_id)})
    audit(db, actor_id=member.user_id, workspace_id=member.workspace_id, action="investigation_started", resource_type="investigation", resource_id=str(run.id))
    db.commit()
    return {"investigation_id": str(run.id), "state": incident.state}


@router.post("/incidents/{incident_id}/cancel")
def cancel(incident_id: UUID, member: Membership = Depends(require("operator")), db: Session = Depends(get_session)) -> dict:
    incident = db.scalar(
        select(Incident).where(Incident.id == incident_id, Incident.workspace_id == member.workspace_id).with_for_update()
    )
    if incident is None:
        _error(404, "not_found", "Incident not found")
    intents = db.scalars(
        select(RepairIntent)
        .join(Proposal, Proposal.id == RepairIntent.proposal_id)
        .where(Proposal.incident_id == incident.id, RepairIntent.workspace_id == member.workspace_id)
        .with_for_update()
    ).all()
    if any(row.state in {"DISPATCHING", "OUTCOME_UNKNOWN", "SUCCEEDED", "RECONCILING", "VERIFIED"} for row in intents) or incident.state in {"EXECUTING", "VERIFYING"}:
        _error(409, "busy", "A write may already have committed. Cancellation cannot undo it.")
    for row in intents:
        if row.state == "PREPARED":
            row.state = "FAILED"
            row.updated_at = utcnow()
    proposals = db.scalars(select(Proposal).where(Proposal.incident_id == incident.id, Proposal.decision_state == "open")).all()
    for proposal in proposals:
        proposal.decision_state = "cancelled"
    incident.state = "OPEN"
    incident.updated_at = utcnow()
    runs = db.scalars(
        select(InvestigationRun).where(
            InvestigationRun.incident_id == incident.id,
            InvestigationRun.state.in_(["RUNNING", "AWAITING_APPROVAL"]),
        )
    ).all()
    if incident.active_investigation_id:
        active = db.get(InvestigationRun, incident.active_investigation_id)
        if active is not None and active not in runs and active.state != "COMPLETED":
            runs.append(active)
    for run in runs:
        run.state = "CANCELLED"
        run.finished_at = utcnow()
    release_pending(db, kind="investigate", payload_key="investigation_id", payload_values={str(run.id) for run in runs})
    audit(db, actor_id=member.user_id, workspace_id=member.workspace_id, action="incident_cancelled", resource_type="incident", resource_id=str(incident.id))
    db.commit()
    return _incident_detail(db, incident)


@router.post("/incidents/{incident_id}/reopen")
def reopen(incident_id: UUID, member: Membership = Depends(require("operator")), db: Session = Depends(get_session)) -> dict:
    incident = _incident(db, member, incident_id)
    if incident.state not in {"RESOLVED", "ESCALATED", "NEEDS_INFORMATION"}:
        _error(409, "invalid_state", "This incident cannot be reopened from its current state")
    incident.state = "OPEN"
    incident.updated_at = utcnow()
    audit(db, actor_id=member.user_id, workspace_id=member.workspace_id, action="incident_reopened", resource_type="incident", resource_id=str(incident.id))
    db.commit()
    return _incident_detail(db, incident)


@router.get("/incidents/{incident_id}/export")
def export_incident(incident_id: UUID, member: Membership = Depends(require("viewer")), db: Session = Depends(get_session)) -> dict:
    incident = _incident(db, member, incident_id)
    return _incident_detail(db, incident)


@router.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: UUID, member: Membership = Depends(require("viewer")), db: Session = Depends(get_session)) -> dict:
    run = db.scalar(
        select(InvestigationRun).where(InvestigationRun.id == investigation_id, InvestigationRun.workspace_id == member.workspace_id)
    )
    if run is None:
        _error(404, "not_found", "Investigation not found")
    diagnosis = db.scalar(select(Diagnosis).where(Diagnosis.investigation_run_id == run.id))
    evidence = db.scalars(select(Evidence).where(Evidence.investigation_run_id == run.id)).all()
    proposal = db.scalar(select(Proposal).where(Proposal.investigation_run_id == run.id))
    return {
        "id": str(run.id),
        "mode": run.mode,
        "provider": run.provider,
        "model": run.model,
        "workflow_version": run.workflow_version,
        "state": run.state,
        "summary": run.summary,
        "usage": run.usage,
        "tool_trace": run.tool_trace,
        "diagnosis": _diagnosis_view(diagnosis) if diagnosis else None,
        "evidence": [_evidence_view(item) for item in evidence],
        "proposal_id": str(proposal.id) if proposal else None,
    }


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: UUID, member: Membership = Depends(require("viewer")), db: Session = Depends(get_session)) -> dict:
    proposal = _proposal(db, member, proposal_id)
    return _proposal_view(db, proposal)


@router.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: UUID, payload: DecisionIn, member: Membership = Depends(require("approver")), db: Session = Depends(get_session)) -> dict:
    return _decide(db, member, proposal_id, payload, "approved")


@router.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: UUID, payload: DecisionIn, member: Membership = Depends(require("approver")), db: Session = Depends(get_session)) -> dict:
    return _decide(db, member, proposal_id, payload, "rejected")


def _decide(db: Session, member: Membership, proposal_id: UUID, payload: DecisionIn, decision: str) -> dict:
    proposal = db.scalar(
        select(Proposal).where(Proposal.id == proposal_id, Proposal.workspace_id == member.workspace_id).with_for_update()
    )
    if proposal is None:
        _error(404, "not_found", "Proposal not found")
    incident = db.scalar(select(Incident).where(Incident.id == proposal.incident_id).with_for_update())
    if decision == "approved" and get_settings().cir_environment == "production":
        initiator = db.scalar(select(AuditEvent.actor_id).where(
            AuditEvent.workspace_id == member.workspace_id,
            AuditEvent.action == "investigation_started",
            AuditEvent.resource_id == str(proposal.investigation_run_id),
        ).order_by(AuditEvent.created_at.desc()).limit(1))
        if initiator is None or initiator == member.user_id:
            _error(403, "separate_approver", "Production approval requires a different authenticated user account from the investigator")
    if payload.proposal_hash != proposal.proposal_hash:
        _error(409, "stale_proposal", "The proposal hash does not match. Refresh and review it again.")
    if proposal.decision_state == "approved" and decision == "rejected":
        intent = db.scalar(select(RepairIntent).where(RepairIntent.proposal_id == proposal.id).with_for_update())
        if intent is not None and intent.state == "PREPARED":
            db.add(
                Approval(
                    workspace_id=member.workspace_id,
                    proposal_id=proposal.id,
                    proposal_hash=proposal.proposal_hash,
                    approver_user_id=member.user_id,
                    decision="rejected",
                    reason=payload.reason,
                    created_at=utcnow(),
                )
            )
            proposal.decision_state = "rejected"
            intent.state = "FAILED"
            intent.updated_at = utcnow()
            incident.state = "NEEDS_INFORMATION"
            incident.updated_at = utcnow()
            audit(db, actor_id=member.user_id, workspace_id=member.workspace_id, action="proposal_rejected", resource_type="proposal", resource_id=str(proposal.id), resource_version=proposal.proposal_hash, metadata={"stopped_intent": str(intent.id)})
            db.commit()
            return _proposal_view(db, proposal)
        _error(409, "already_dispatched", "This approval can no longer be rejected. A write may already have committed.")
    if proposal.decision_state != "open":
        _error(409, "already_decided", f"This proposal is {proposal.decision_state}.")
    if incident.active_investigation_id != proposal.investigation_run_id or incident.state != "AWAITING_APPROVAL":
        _error(409, "superseded", "This proposal is not the active investigation.")
    if hash_proposal_fields(
        workspace_id=str(proposal.workspace_id),
        incident_id=str(proposal.incident_id),
        customer_link_id=str(proposal.customer_link_id),
        connector_id=str(proposal.connector_id),
        mapping_version=proposal.mapping_version,
        before=proposal.before_value,
        after=proposal.after_value,
        source_preconditions=proposal.source_preconditions,
        destination_preconditions=proposal.destination_preconditions,
        evidence_ids=list(proposal.evidence_ids or []),
        expires_at=isoformat_utc(proposal.expires_at),
        version=proposal.version,
    ) != proposal.proposal_hash:
        _error(409, "hash_mismatch", "Stored proposal content does not match its hash")
    if proposal.expires_at < utcnow():
        _error(409, "expired", "This proposal has expired. Investigate again.")
    existing = db.scalar(select(Approval).where(Approval.proposal_id == proposal.id, Approval.decision == "approved"))
    if existing and decision == "approved":
        _error(409, "already_decided", "This proposal was already approved")
    approval = Approval(
        workspace_id=member.workspace_id,
        proposal_id=proposal.id,
        proposal_hash=proposal.proposal_hash,
        approver_user_id=member.user_id,
        decision=decision,
        reason=payload.reason,
        created_at=utcnow(),
    )
    db.add(approval)
    db.flush()
    proposal.decision_state = "rejected" if decision == "rejected" else "approved"
    if decision == "rejected":
        incident.state = "NEEDS_INFORMATION"
        incident.updated_at = utcnow()
        audit(db, actor_id=member.user_id, workspace_id=member.workspace_id, action="proposal_rejected", resource_type="proposal", resource_id=str(proposal.id), resource_version=proposal.proposal_hash)
        db.commit()
        return _proposal_view(db, proposal)
    link = db.get(CustomerLink, proposal.customer_link_id)
    repair_payload = {
        "account_id": link.destination_customer_id,
        "plan": proposal.after_value,
        "expected_revision": proposal.destination_preconditions.get("revision"),
        "source_revision": proposal.source_preconditions.get("revision"),
    }
    intent = RepairIntent(
        workspace_id=member.workspace_id,
        proposal_id=proposal.id,
        approval_id=approval.id,
        idempotency_key=f"repair-{proposal.id}",
        payload=repair_payload,
        payload_hash=canonical_hash(repair_payload),
        state="PREPARED",
        reconciliation={},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(intent)
    db.flush()
    incident.state = "EXECUTING"
    incident.updated_at = utcnow()
    enqueue(db, workspace_id=member.workspace_id, kind="execute_repair", payload={"intent_id": str(intent.id)})
    audit(
        db,
        actor_id=member.user_id,
        workspace_id=member.workspace_id,
        action="proposal_approved",
        resource_type="proposal",
        resource_id=str(proposal.id),
        resource_version=proposal.proposal_hash,
        metadata={"repair_intent_id": str(intent.id)},
    )
    db.commit()
    return {"proposal": _proposal_view(db, proposal), "repair_id": str(intent.id)}


@router.get("/repairs/{repair_id}")
def get_repair(repair_id: UUID, member: Membership = Depends(require("viewer")), db: Session = Depends(get_session)) -> dict:
    intent = db.scalar(select(RepairIntent).where(RepairIntent.id == repair_id, RepairIntent.workspace_id == member.workspace_id))
    if intent is None:
        _error(404, "not_found", "Repair not found")
    attempts = db.scalars(select(RepairAttempt).where(RepairAttempt.repair_intent_id == intent.id)).all()
    verification = db.scalar(select(Verification).where(Verification.repair_intent_id == intent.id))
    return {
        "id": str(intent.id),
        "state": intent.state,
        "idempotency_key": intent.idempotency_key,
        "payload_hash": intent.payload_hash,
        "reconciliation": intent.reconciliation,
        "attempts": [
            {
                "response_class": item.response_class,
                "operation": item.operation,
                "destination_receipt_id": item.destination_receipt_id,
                "at": item.created_at.isoformat(),
            }
            for item in attempts
        ],
        "verification": None
        if verification is None
        else {
            "outcome": verification.outcome,
            "mismatch": verification.mismatch,
            "duplicate_write": verification.duplicate_write,
            "limits": verification.limits,
            "observations": verification.observations,
            "at": verification.created_at.isoformat(),
        },
    }


@router.get("/connectors")
def list_connectors(member: Membership = Depends(require("viewer")), db: Session = Depends(get_session)) -> list[dict]:
    rows = db.scalars(select(ConnectorConfig).where(ConnectorConfig.workspace_id == member.workspace_id)).all()
    return [_connector_view(row) for row in rows]


@router.post("/connectors")
def create_connector(payload: ConnectorIn, member: Membership = Depends(require("administrator")), db: Session = Depends(get_session)) -> dict:
    if payload.provider not in ALLOWED_PROVIDERS:
        _error(422, "invalid_provider", "Provider is not allowlisted")
    settings = get_settings()
    secret_configured = payload.provider != "hubspot" or bool(settings.hubspot_token)
    row = ConnectorConfig(
        workspace_id=member.workspace_id,
        name=payload.name,
        provider=payload.provider,
        secret_configured=secret_configured,
        settings={"plan_property": payload.plan_property, "mapping_version": payload.mapping_version},
        capabilities={"read": True, "write": payload.provider == "support_service"},
        created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    audit(db, actor_id=member.user_id, workspace_id=member.workspace_id, action="connector_created", resource_type="connector", resource_id=str(row.id), metadata={"provider": payload.provider})
    db.commit()
    return _connector_view(row)


@router.post("/connectors/{connector_id}/health")
def connector_health(connector_id: UUID, member: Membership = Depends(require("viewer")), db: Session = Depends(get_session)) -> dict:
    row = db.scalar(select(ConnectorConfig).where(ConnectorConfig.id == connector_id, ConnectorConfig.workspace_id == member.workspace_id))
    if row is None:
        _error(404, "not_found", "Connector not found")
    import httpx

    settings = get_settings()
    if row.provider == "hubspot":
        status = "not_configured" if not settings.hubspot_token else "configured_unread"
    else:
        base = settings.cir_crm_base_url if row.provider == "crm_simulator" else settings.cir_support_base_url
        try:
            response = httpx.get(f"{base}/health/ready", timeout=3)
            status = "ok" if response.status_code == 200 else "down"
        except httpx.HTTPError:
            status = "down"
    row.last_health_status = status
    row.last_health_at = utcnow()
    db.commit()
    return _connector_view(row)


@router.post("/events/{connector_id}")
async def receive_event(connector_id: UUID, request: Request, db: Session = Depends(get_session)) -> dict:
    raw = await request.body()
    if len(raw) > 65536:
        _error(413, "too_large", "Event body is too large")
    result = ingest_event(db, connector_id, raw, request.headers.get("x-signature"))
    if result["status"] >= 400:
        raise HTTPException(status_code=result["status"], detail=result["body"]["error"])
    return result["body"]


@router.post("/reconcile")
def reconcile(member: Membership = Depends(require("operator")), db: Session = Depends(get_session)) -> dict:
    opened = reconcile_workspace(db, member.workspace_id)
    return {"incidents_opened": opened}


@router.get("/evaluations")
def list_evaluations(member: Membership = Depends(require("viewer")), db: Session = Depends(get_session)) -> dict:
    rows = db.scalars(select(EvaluationReport).order_by(EvaluationReport.created_at.desc()).limit(20)).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "approach": row.approach,
                "mode": row.mode,
                "provider": row.provider,
                "model": row.model,
                "split": row.split,
                "summary": row.summary,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "live_status": "measured raw-model baseline; review limitations" if any(row.mode == "live" for row in rows) else "no live evaluation report imported",
    }


@router.post("/demo/scenarios")
def demo_scenario(payload: ScenarioIn, member: Membership = Depends(require("administrator")), db: Session = Depends(get_session)) -> dict:
    workspace = db.get(Workspace, member.workspace_id)
    try:
        assert_demo_workspace(workspace)
        result = apply_scenario(db, workspace, payload.scenario)
    except PermissionError:
        _error(403, "forbidden", "Demo reset is limited to the synthetic northstar-demo workspace")
    except ValueError:
        _error(422, "unknown_scenario", "Unknown scenario")
    audit(db, actor_id=member.user_id, workspace_id=workspace.id, action="demo_scenario_applied", resource_type="workspace", resource_id=str(workspace.id), metadata={"scenario": payload.scenario})
    db.commit()
    return result


@router.post("/demo/reset")
def demo_reset(member: Membership = Depends(require("administrator")), db: Session = Depends(get_session)) -> dict:
    workspace = db.get(Workspace, member.workspace_id)
    try:
        assert_demo_workspace(workspace)
    except PermissionError:
        _error(403, "forbidden", "Demo reset is limited to the synthetic northstar-demo workspace")
    previous = db.scalars(
        select(Incident).where(
            Incident.workspace_id == workspace.id,
            Incident.state.in_(["OPEN", "INVESTIGATING", "NEEDS_INFORMATION", "AWAITING_APPROVAL"]),
        )
    ).all()
    for row in previous:
        row.state = "ESCALATED"
        row.updated_at = utcnow()
    db.commit()
    result = apply_scenario(db, workspace, "missing_update")
    audit(db, actor_id=member.user_id, workspace_id=workspace.id, action="demo_reset", resource_type="workspace", resource_id=str(workspace.id))
    db.commit()
    return result


@router.get("/activity")
def activity(member: Membership = Depends(require("viewer")), db: Session = Depends(get_session)) -> dict:
    rows = db.scalars(
        select(AuditEvent).where(AuditEvent.workspace_id == member.workspace_id).order_by(AuditEvent.created_at.desc()).limit(100)
    ).all()
    return {
        "items": [
            {
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "at": row.created_at.isoformat(),
                "metadata": row.metadata_json,
            }
            for row in rows
        ]
    }


@router.get("/observability/traces")
def traces(member: Membership = Depends(require("administrator"))) -> dict:
    path = get_settings().cir_trace_path
    items = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle.readlines()[-50:]:
                import json

                items.append(json.loads(line))
    except FileNotFoundError:
        items = []
    workspace_id = str(member.workspace_id)
    visible = [item for item in items if str((item.get("attributes") or {}).get("workspace_id") or "") == workspace_id]
    return {
        "items": visible,
        "note": "Spans are redacted and limited to this workspace. This is a local trace view, not a production collector.",
    }


def _incident(db: Session, member: Membership, incident_id: UUID) -> Incident:
    row = db.scalar(select(Incident).where(Incident.id == incident_id, Incident.workspace_id == member.workspace_id))
    if row is None:
        _error(404, "not_found", "Incident not found")
    return row


def _proposal(db: Session, member: Membership, proposal_id: UUID) -> Proposal:
    row = db.scalar(select(Proposal).where(Proposal.id == proposal_id, Proposal.workspace_id == member.workspace_id))
    if row is None:
        _error(404, "not_found", "Proposal not found")
    return row


def _incident_brief(db: Session, incident: Incident) -> dict:
    link = db.get(CustomerLink, incident.customer_link_id) if incident.customer_link_id else None
    age_seconds = int((utcnow() - incident.created_at).total_seconds())
    owner = db.get(User, incident.owner_user_id) if incident.owner_user_id else None
    return {
        "id": str(incident.id),
        "title": incident.title,
        "state": incident.state,
        "severity": incident.severity,
        "account": link.display_name if link else "Unmapped report",
        "owner": owner.username if owner else "Unassigned",
        "age_seconds": max(age_seconds, 0),
        "created_at": incident.created_at.isoformat(),
    }


def _incident_detail(db: Session, incident: Incident) -> dict:
    link = db.get(CustomerLink, incident.customer_link_id) if incident.customer_link_id else None
    run = db.get(InvestigationRun, incident.active_investigation_id) if incident.active_investigation_id else None
    diagnosis = db.scalar(select(Diagnosis).where(Diagnosis.investigation_run_id == run.id)) if run else None
    proposal = db.scalar(select(Proposal).where(Proposal.incident_id == incident.id).order_by(Proposal.created_at.desc()))
    repair = None
    verification = None
    if proposal:
        repair = db.scalar(select(RepairIntent).where(RepairIntent.proposal_id == proposal.id))
        if repair:
            verification = db.scalar(select(Verification).where(Verification.repair_intent_id == repair.id))
    return {
        **_incident_brief(db, incident),
        "description": incident.description,
        "customer_link_id": str(link.id) if link else None,
        "problem": run.summary if run and run.summary else incident.description,
        "investigation": None
        if run is None
        else {"id": str(run.id), "state": run.state, "mode": run.mode, "provider": run.provider, "model": run.model},
        "diagnosis": _diagnosis_view(diagnosis) if diagnosis else None,
        "proposal": _proposal_view(db, proposal) if proposal else None,
        "repair_id": str(repair.id) if repair else None,
        "repair_state": repair.state if repair else None,
        "verification": None
        if verification is None
        else {"outcome": verification.outcome, "limits": verification.limits, "mismatch": verification.mismatch},
    }


def _diagnosis_view(row: Diagnosis) -> dict:
    return {
        "cause": row.cause,
        "next_action": row.next_action,
        "certainty": row.certainty,
        "certainty_note": "This is a category, not a calibrated probability.",
        "limitations": row.limitations,
        "observations": row.observations,
        "competing_explanations": row.competing,
        "evidence_ids": row.evidence_ids,
    }


def _evidence_view(row: Evidence) -> dict:
    return {
        "id": str(row.id),
        "source": row.source,
        "observed_at": row.observed_at.isoformat(),
        "revision": row.revision,
        "content": row.sanitized_content,
        "fingerprint": row.content_fingerprint,
    }


def _proposal_view(db: Session, proposal: Proposal) -> dict:
    link = db.get(CustomerLink, proposal.customer_link_id)
    approvals = db.scalars(select(Approval).where(Approval.proposal_id == proposal.id)).all()
    return {
        "id": str(proposal.id),
        "account": link.display_name if link else "Unknown",
        "field": proposal.field_name,
        "before": proposal.before_value,
        "after": proposal.after_value,
        "proposal_hash": proposal.proposal_hash,
        "decision_state": proposal.decision_state,
        "version": proposal.version,
        "mapping_version": proposal.mapping_version,
        "expires_at": proposal.expires_at.isoformat(),
        "source_preconditions": proposal.source_preconditions,
        "destination_preconditions": proposal.destination_preconditions,
        "evidence_ids": proposal.evidence_ids,
        "approvals": [
            {"decision": item.decision, "reason": item.reason, "at": item.created_at.isoformat(), "approver_id": str(item.approver_user_id)}
            for item in approvals
        ],
    }


def _connector_view(row: ConnectorConfig) -> dict:
    settings = get_settings()
    if row.provider == "hubspot":
        origin = "https://api.hubapi.com"
    elif row.provider == "crm_simulator":
        origin = settings.cir_crm_base_url
    else:
        origin = settings.cir_support_base_url
    return {
        "id": str(row.id),
        "name": row.name,
        "provider": row.provider,
        "origin_host": origin.split("://", 1)[-1].split("/")[0],
        "secret_configured": row.secret_configured,
        "capabilities": row.capabilities,
        "settings": {"mapping_version": (row.settings or {}).get("mapping_version"), "plan_property": (row.settings or {}).get("plan_property")},
        "last_health_status": row.last_health_status,
        "last_health_at": row.last_health_at.isoformat() if row.last_health_at else None,
        "setup_note": "HubSpot tokens stay in the server environment. This screen never shows them."
        if row.provider == "hubspot"
        else "Synthetic connector. Credentials are local process settings.",
    }
