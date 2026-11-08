"""Restricted repair executor. It sends one allowlisted plan write and then re-reads."""

from __future__ import annotations

import os
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from recovery_api.clients import ClientError, ServiceClient, support_patch_plan, support_receipt
from recovery_api.models import Approval, CustomerLink, Incident, Proposal, RepairAttempt, RepairIntent, User, Verification
from recovery_api.records import audit, read_crm, read_support, utcnow
from recovery_api.security import has_role, membership_for
from recovery_api.settings import get_settings
from recovery_core.hashing import canonical_hash
from recovery_core.proposals import hash_proposal_fields, isoformat_utc
from recovery_core.redact import sanitize


def maybe_crash(point: str) -> None:
    if os.environ.get("CIR_CRASH_POINT") == point:
        os._exit(86)


def _support_client() -> ServiceClient:
    settings = get_settings()
    return ServiceClient(settings.cir_support_base_url, settings.support_token, timeout=3.0)


def _attempt(db: Session, intent: RepairIntent, response_class: str, body: dict, duration_ms: int = 0, receipt_id: str | None = None) -> None:
    db.add(
        RepairAttempt(
            workspace_id=intent.workspace_id,
            repair_intent_id=intent.id,
            operation="patch_plan",
            response_class=response_class,
            duration_ms=duration_ms,
            destination_receipt_id=receipt_id,
            sanitized=sanitize(body),
            created_at=utcnow(),
        )
    )


def _fail(db: Session, intent: RepairIntent, incident: Incident, state: str, incident_state: str, reason: str) -> None:
    intent.state = state
    intent.updated_at = utcnow()
    incident.state = incident_state
    incident.updated_at = utcnow()
    audit(
        db,
        workspace_id=intent.workspace_id,
        action="repair_stopped",
        resource_type="repair_intent",
        resource_id=str(intent.id),
        metadata={"reason": reason, "state": state},
    )
    db.commit()


def stored_hash(proposal: Proposal) -> str:
    return hash_proposal_fields(
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
    )


def _lookup_receipt(intent: RepairIntent) -> dict | None:
    status, body = support_receipt(_support_client(), intent.idempotency_key)
    if status == 404:
        return None
    if status != 200:
        return {"status_code": status, "payload_hash": None, "response": body}
    return body


def _verify(db: Session, intent: RepairIntent, proposal: Proposal, incident: Incident, link: CustomerLink, pre_write_count: int | None) -> None:
    intent.state = "RECONCILING"
    incident.state = "VERIFYING"
    db.commit()
    maybe_crash("after_reconciling")
    source = read_crm(db, link, investigation_id=None)
    dest = read_support(db, link, investigation_id=None, source_name="verification")
    if source.get("status") in {"unavailable", "timeout"} or dest.get("status") in {"unavailable", "timeout"}:
        outcome = "verification_unavailable"
        intent.state = "ESCALATED"
        incident.state = "ESCALATED"
        limits = "A fresh read was unavailable after the write. No additional write was sent."
        incident.updated_at = utcnow()
        intent.updated_at = utcnow()
        intent.reconciliation = {
            "source_status": source.get("status"),
            "destination_plan": dest.get("plan"),
            "write_count": dest.get("write_count"),
            "duplicate_write": False,
        }
        db.add(
            Verification(
                workspace_id=intent.workspace_id,
                repair_intent_id=intent.id,
                incident_id=incident.id,
                observations={"source": sanitize(source), "destination": sanitize(dest)},
                mismatch=True,
                duplicate_write=False,
                outcome=outcome,
                limits=limits,
                created_at=utcnow(),
            )
        )
        audit(
            db,
            workspace_id=intent.workspace_id,
            action="repair_not_verified",
            resource_type="repair_intent",
            resource_id=str(intent.id),
            metadata={"outcome": outcome},
        )
        db.commit()
        return
    expected = proposal.after_value
    source_same = (
        source.get("status") == "ok"
        and source.get("revision") == proposal.source_preconditions.get("revision")
        and source.get("plan") == proposal.source_preconditions.get("plan")
        and source.get("fingerprint") == proposal.source_preconditions.get("fingerprint")
    )
    dest_ok = dest.get("status") == "ok" and dest.get("plan") == expected
    write_count = dest.get("write_count")
    duplicate = False
    if pre_write_count is not None and isinstance(write_count, int) and write_count > pre_write_count + 1:
        duplicate = True
    if dest_ok and source_same and not duplicate:
        outcome = "verified"
        intent.state = "VERIFIED"
        incident.state = "RESOLVED"
        limits = "Checked the plan field, source revision, source fingerprint, and destination revision. Other fields were not in scope."
    elif dest_ok and not source_same:
        outcome = "source_changed"
        intent.state = "ESCALATED"
        incident.state = "OPEN"
        limits = "The destination shows the approved plan, but the CRM changed before verification. The incident is open again."
    else:
        outcome = "mismatch"
        intent.state = "ESCALATED"
        incident.state = "ESCALATED"
        limits = "Fresh reads did not show the approved plan on an unchanged source."
    incident.updated_at = utcnow()
    intent.updated_at = utcnow()
    intent.reconciliation = {
        "source_status": source.get("status"),
        "destination_plan": dest.get("plan"),
        "destination_revision": dest.get("revision"),
        "write_count": write_count,
        "duplicate_write": duplicate,
    }
    db.add(
        Verification(
            workspace_id=intent.workspace_id,
            repair_intent_id=intent.id,
            incident_id=incident.id,
            observations={"source": sanitize(source), "destination": sanitize(dest)},
            mismatch=outcome != "verified",
            duplicate_write=duplicate,
            outcome=outcome,
            limits=limits,
            created_at=utcnow(),
        )
    )
    audit(
        db,
        workspace_id=intent.workspace_id,
        action="repair_verified" if outcome == "verified" else "repair_not_verified",
        resource_type="repair_intent",
        resource_id=str(intent.id),
        metadata={"outcome": outcome},
    )
    db.commit()


def _authorization_holds(db: Session, intent: RepairIntent, proposal: Proposal, approval: Approval, incident: Incident, link: CustomerLink) -> bool:
    approver = db.get(User, approval.approver_user_id) if approval is not None else None
    membership = membership_for(db, approver, intent.workspace_id) if approver else None
    return not (
        proposal is None
        or approval is None
        or incident is None
        or link is None
        or membership is None
        or not has_role(membership, "approver")
        or approval.decision != "approved"
        or proposal.decision_state != "approved"
        or approval.proposal_hash != proposal.proposal_hash
        or stored_hash(proposal) != proposal.proposal_hash
        or proposal.field_name != "plan"
        or proposal.connector_id != link.destination_connector_id
        or canonical_hash(intent.payload) != intent.payload_hash
    )


def _continue_committed(db: Session, intent: RepairIntent, proposal: Proposal, incident: Incident, link: CustomerLink) -> None:
    """Verify a write that already committed. This path does not send another patch."""
    _verify(db, intent, proposal, incident, link, (intent.reconciliation or {}).get("pre_write_count"))


def _schedule_retry(db: Session, intent: RepairIntent, incident: Incident, response_class: str, body: dict, retry_after: str | None) -> None:
    from recovery_api.jobs import enqueue
    from recovery_core.backoff import retry_delay_seconds

    settings = get_settings()
    intent.dispatch_attempts = int(intent.dispatch_attempts or 0) + 1
    if intent.retry_deadline is None:
        intent.retry_deadline = utcnow() + timedelta(minutes=15)
    _attempt(db, intent, response_class, body)
    if intent.dispatch_attempts >= settings.cir_repair_max_attempts or utcnow() >= intent.retry_deadline:
        _fail(db, intent, incident, "ESCALATED", "ESCALATED", f"{response_class} retry budget exhausted")
        return
    delay = int(retry_delay_seconds(retry_after, attempt=intent.dispatch_attempts, cap=30))
    intent.state = "PREPARED"
    intent.updated_at = utcnow()
    enqueue(
        db,
        workspace_id=intent.workspace_id,
        kind="execute_repair",
        payload={"intent_id": str(intent.id)},
        delay_seconds=delay,
        max_attempts=1,
    )
    audit(
        db,
        workspace_id=intent.workspace_id,
        action="repair_retry_scheduled",
        resource_type="repair_intent",
        resource_id=str(intent.id),
        metadata={"attempt": intent.dispatch_attempts, "delay_seconds": delay, "cap_seconds": 30},
    )
    db.commit()


def execute_intent(db: Session, intent_id, owner: str, lease_guard=None) -> None:
    if lease_guard is not None and not lease_guard():
        db.rollback()
        raise RuntimeError("worker lease lost before repair")
    intent = db.scalar(select(RepairIntent).where(RepairIntent.id == intent_id).with_for_update())
    if intent is None:
        db.rollback()
        return
    if intent.state in {"VERIFIED", "ESCALATED", "FAILED"}:
        db.rollback()
        return
    proposal = db.get(Proposal, intent.proposal_id)
    approval = db.get(Approval, intent.approval_id)
    incident = db.scalar(select(Incident).where(Incident.id == proposal.incident_id, Incident.workspace_id == intent.workspace_id))
    link = db.scalar(
        select(CustomerLink).where(CustomerLink.id == proposal.customer_link_id, CustomerLink.workspace_id == intent.workspace_id)
    )
    if proposal is None or incident is None or link is None:
        db.rollback()
        return
    if intent.state in {"SUCCEEDED", "RECONCILING"}:
        _continue_committed(db, intent, proposal, incident, link)
        return
    receipt = None
    if intent.state in {"DISPATCHING", "OUTCOME_UNKNOWN"}:
        receipt = _lookup_receipt(intent)
        if receipt and receipt.get("payload_hash") not in {None, intent.payload_hash}:
            _fail(db, intent, incident, "ESCALATED", "ESCALATED", "idempotency payload conflict")
            return
        if receipt and receipt.get("status_code") == 200 and receipt.get("payload_hash") == intent.payload_hash:
            _attempt(db, intent, "replayed_receipt", receipt.get("response") or {}, receipt_id=intent.idempotency_key)
            intent.state = "SUCCEEDED"
            db.commit()
            maybe_crash("after_succeeded")
            _continue_committed(db, intent, proposal, incident, link)
            return
        if proposal.expires_at < utcnow():
            _fail(db, intent, incident, "ESCALATED", "ESCALATED", "expired before the destination receipt was confirmed")
            return
    if not _authorization_holds(db, intent, proposal, approval, incident, link):
        _fail(db, intent, incident, "FAILED", "ESCALATED", "preconditions failed before a write")
        return
    if proposal.expires_at < utcnow():
        _fail(db, intent, incident, "FAILED", "NEEDS_INFORMATION", "proposal expired")
        return

    source = read_crm(db, link, investigation_id=None)
    dest = read_support(db, link, investigation_id=None, source_name="pre_repair")
    if (
        source.get("status") != "ok"
        or source.get("revision") != proposal.source_preconditions.get("revision")
        or source.get("plan") != proposal.source_preconditions.get("plan")
        or source.get("fingerprint") != proposal.source_preconditions.get("fingerprint")
    ):
        _fail(db, intent, incident, "FAILED", "NEEDS_INFORMATION", "source changed after approval")
        return
    if (
        dest.get("status") != "ok"
        or dest.get("revision") != proposal.destination_preconditions.get("revision")
        or dest.get("plan") != proposal.destination_preconditions.get("plan")
    ):
        _fail(db, intent, incident, "FAILED", "ESCALATED", "destination changed after approval")
        return
    if intent.payload.get("plan") != proposal.after_value or intent.payload.get("account_id") != link.destination_customer_id:
        _fail(db, intent, incident, "FAILED", "ESCALATED", "payload does not match the approved proposal")
        return

    now = utcnow()
    if intent.state == "PREPARED":
        claim = db.execute(
            update(RepairIntent)
            .where(RepairIntent.id == intent.id, RepairIntent.state == "PREPARED")
            .values(state="DISPATCHING", dispatch_owner=owner, dispatch_lease_expires=now + timedelta(seconds=30), updated_at=now)
        )
        if claim.rowcount != 1:
            db.rollback()
            return
    elif intent.state in {"DISPATCHING", "OUTCOME_UNKNOWN"}:
        if intent.dispatch_lease_expires and intent.dispatch_lease_expires > now and intent.dispatch_owner not in {None, owner}:
            db.rollback()
            return
        intent.dispatch_owner = owner
        intent.dispatch_lease_expires = now + timedelta(seconds=30)
        intent.state = "DISPATCHING"
    else:
        db.rollback()
        return
    intent.reconciliation = {**(intent.reconciliation or {}), "pre_write_count": dest.get("write_count")}
    incident.state = "EXECUTING"
    db.commit()
    maybe_crash("after_intent")

    # Fencing prevents a known stale worker from starting a new request. An
    # already in-flight HTTP request cannot be recalled; the destination's
    # immutable receipt and conditional revision enforce one side effect there.
    if lease_guard is not None and not lease_guard():
        db.rollback()
        raise RuntimeError("worker lease lost before destination dispatch")

    try:
        status, body = support_patch_plan(
            _support_client(),
            link.destination_customer_id,
            plan=proposal.after_value,
            expected_revision=proposal.destination_preconditions["revision"],
            source_revision=proposal.source_preconditions.get("revision"),
            idempotency_key=intent.idempotency_key,
        )
    except ClientError as exc:
        intent.state = "OUTCOME_UNKNOWN"
        intent.updated_at = utcnow()
        _attempt(db, intent, "timeout", {"error": exc.body.get("error", "unknown")})
        db.commit()
        maybe_crash("after_dispatch")
        recovered = _lookup_receipt(intent)
        if recovered and recovered.get("status_code") == 200 and recovered.get("payload_hash") == intent.payload_hash:
            _attempt(db, intent, "replayed_receipt", recovered.get("response") or {}, receipt_id=intent.idempotency_key)
            intent.state = "SUCCEEDED"
            db.commit()
            _verify(db, intent, proposal, incident, link, dest.get("write_count"))
            return
        audit(
            db,
            workspace_id=intent.workspace_id,
            action="repair_outcome_unknown",
            resource_type="repair_intent",
            resource_id=str(intent.id),
            metadata={"reason": "response lost or timed out; receipt not confirmed"},
        )
        incident.state = "ESCALATED"
        intent.state = "ESCALATED"
        db.commit()
        return

    maybe_crash("after_dispatch")
    if status == 200:
        _attempt(db, intent, "success", body, receipt_id=body.get("receipt_id"))
        intent.state = "SUCCEEDED"
        db.commit()
        maybe_crash("after_succeeded")
        _verify(db, intent, proposal, incident, link, dest.get("write_count"))
        return
    if status == 409 and body.get("error") == "conflict":
        _attempt(db, intent, "conflict", body)
        _fail(db, intent, incident, "FAILED", "ESCALATED", "destination conditional-write conflict")
        return
    if status == 429:
        _schedule_retry(db, intent, incident, "rate_limited", body, str(body.get("retry_after") or "") or None)
        return
    if status >= 500:
        recovered = _lookup_receipt(intent)
        if recovered and recovered.get("status_code") == 200 and recovered.get("payload_hash") == intent.payload_hash:
            _attempt(db, intent, "replayed_receipt", recovered.get("response") or {}, receipt_id=intent.idempotency_key)
            intent.state = "SUCCEEDED"
            db.commit()
            maybe_crash("after_succeeded")
            _verify(db, intent, proposal, incident, link, dest.get("write_count"))
            return
        _schedule_retry(db, intent, incident, "unavailable", body, None)
        return
    _attempt(db, intent, "error", body)
    _fail(db, intent, incident, "FAILED", "ESCALATED", f"destination returned {status}")
