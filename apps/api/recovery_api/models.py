import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

INCIDENT_STATES = (
    "OPEN",
    "INVESTIGATING",
    "NEEDS_INFORMATION",
    "AWAITING_APPROVAL",
    "EXECUTING",
    "VERIFYING",
    "RESOLVED",
    "ESCALATED",
)
REPAIR_STATES = (
    "PREPARED",
    "DISPATCHING",
    "SUCCEEDED",
    "FAILED",
    "OUTCOME_UNKNOWN",
    "RECONCILING",
    "VERIFIED",
    "ESCALATED",
)
ROLES = ("viewer", "operator", "approver", "administrator")


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (UniqueConstraint("user_id", "workspace_id", name="uq_membership"),)


class SessionToken(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    client_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class ConnectorConfig(Base):
    __tablename__ = "connector_configs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    secret_configured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_health_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (UniqueConstraint("workspace_id", "provider", "name", name="uq_connector"),)


class CustomerLink(Base):
    __tablename__ = "customer_links"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    source_connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connector_configs.id"), nullable=False)
    source_customer_id: Mapped[str] = mapped_column(String(80), nullable=False)
    destination_connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connector_configs.id"), nullable=False)
    destination_customer_id: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_connector_id", "source_customer_id", name="uq_source_customer"),
        UniqueConstraint(
            "workspace_id", "destination_connector_id", "destination_customer_id", name="uq_dest_customer"
        ),
    )


class SyncEvent(Base):
    __tablename__ = "sync_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connector_configs.id"), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_customer_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (UniqueConstraint("connector_id", "external_event_id", name="uq_external_event"),)


class SyncAttempt(Base):
    __tablename__ = "sync_attempts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    sync_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sync_events.id"), nullable=True)
    customer_link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_links.id"), nullable=True)
    external_event_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sanitized_response: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    customer_link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_links.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="OPEN")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    active_investigation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        CheckConstraint(f"state IN {INCIDENT_STATES}", name="ck_incident_state"),
        CheckConstraint("severity IN ('low', 'medium', 'high')", name="ck_incident_severity"),
        Index("ix_incidents_workspace_state_created", "workspace_id", "state", "created_at"),
    )


class InvestigationRun(Base):
    __tablename__ = "investigation_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workflow_version: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    budget: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    tool_trace: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    investigation_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("investigation_runs.id"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sanitized_content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sensitivity: Mapped[str] = mapped_column(String(40), nullable=False, default="synthetic")


class Diagnosis(Base):
    __tablename__ = "diagnoses"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    investigation_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigation_runs.id"), nullable=False, index=True)
    cause: Mapped[str] = mapped_column(String(60), nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    competing: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    limitations: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_action: Mapped[str] = mapped_column(String(40), nullable=False)
    certainty: Mapped[str] = mapped_column(String(40), nullable=False)
    observations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), nullable=False, index=True)
    investigation_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigation_runs.id"), nullable=False)
    customer_link_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_links.id"), nullable=False)
    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connector_configs.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(40), nullable=False)
    before_value: Mapped[str] = mapped_column(String(80), nullable=False)
    after_value: Mapped[str] = mapped_column(String(80), nullable=False)
    source_preconditions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    destination_preconditions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    decision_state: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"), nullable=False, index=True)
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approver_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (CheckConstraint("decision IN ('approved', 'rejected')", name="ck_approval_decision"),)


class RepairIntent(Base):
    __tablename__ = "repair_intents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"), nullable=False)
    approval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approvals.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="PREPARED")
    dispatch_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    dispatch_lease_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciliation: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retry_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (CheckConstraint(f"state IN {REPAIR_STATES}", name="ck_repair_state"),)


class RepairAttempt(Base):
    __tablename__ = "repair_attempts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    repair_intent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repair_intents.id"), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    response_class: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    destination_receipt_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sanitized: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class Verification(Base):
    __tablename__ = "verifications"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    repair_intent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repair_intents.id"), nullable=False, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    observations: Mapped[dict] = mapped_column(JSONB, nullable=False)
    mismatch: Mapped[bool] = mapped_column(Boolean, nullable=False)
    duplicate_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    limits: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class WorkerJob(Base):
    __tablename__ = "worker_jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    trace_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    last_duration_ms: Mapped[float | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    __table_args__ = (
        CheckConstraint("state IN ('pending', 'leased', 'done', 'failed', 'dead')", name="ck_job_state"),
        Index("ix_jobs_claim", "state", "scheduled_at"),
    )


class RunbookDoc(Base):
    __tablename__ = "runbook_docs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("slug", "version", name="uq_runbook"),)


class EvaluationReport(Base):
    __tablename__ = "evaluation_reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    approach: Mapped[str] = mapped_column(String(40), nullable=False)
    mode: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    split: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
