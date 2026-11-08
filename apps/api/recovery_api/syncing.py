"""Signed CRM events. Older revisions cannot overwrite a newer destination."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recovery_api.clients import ServiceClient, support_patch_plan
from recovery_api.jobs import enqueue
from recovery_api.models import ConnectorConfig, CustomerLink, SyncAttempt, SyncEvent
from recovery_api.records import audit, utcnow
from recovery_api.settings import get_settings
from recovery_core.hashing import canonical_hash
from recovery_core.mapping import normalize_plan
from recovery_core.signing import signatures_match
from sqlalchemy import select


def ingest_event(db: Session, connector_id, raw: bytes, signature: str | None) -> dict:
    settings = get_settings()
    connector = db.get(ConnectorConfig, connector_id)
    if connector is None or connector.provider != "crm_simulator":
        return {"status": 404, "body": {"error": {"code": "not_found", "message": "Connector not found"}}}
    if not signatures_match(settings.cir_webhook_secret, raw, signature):
        return {"status": 401, "body": {"error": {"code": "bad_signature", "message": "Signature was rejected"}}}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": 400, "body": {"error": {"code": "invalid_json", "message": "Event body was not valid JSON"}}}
    event_id = str(payload.get("event_id") or "")
    customer_id = str(payload.get("customer_id") or "")
    if not event_id or not customer_id:
        return {"status": 400, "body": {"error": {"code": "invalid_event", "message": "event_id and customer_id are required"}}}
    revision = payload.get("revision")
    link = db.scalar(
        select(CustomerLink).where(
            CustomerLink.workspace_id == connector.workspace_id,
            CustomerLink.source_connector_id == connector.id,
            CustomerLink.source_customer_id == customer_id,
        )
    )
    existing = db.scalar(
        select(SyncEvent).where(SyncEvent.connector_id == connector.id, SyncEvent.external_event_id == event_id)
    )
    if existing is None:
        existing = SyncEvent(
            workspace_id=connector.workspace_id,
            connector_id=connector.id,
            external_event_id=event_id,
            source_customer_id=customer_id,
            source_revision=revision if isinstance(revision, int) else None,
            payload_hash=canonical_hash(payload),
            received_at=utcnow(),
        )
        db.add(existing)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            _attempt(db, connector, link, event_id, revision, "rejected_duplicate", {"duplicate": True})
            db.commit()
            return {"status": 200, "body": {"duplicate": True, "applied": False}}
    else:
        _attempt(db, connector, link, event_id, revision, "rejected_duplicate", {"duplicate": True})
        db.commit()
        return {"status": 200, "body": {"duplicate": True, "applied": False}}

    if link is None:
        _attempt(db, connector, None, event_id, revision, "rejected_unknown", {"reason": "no customer link"})
        db.commit()
        return {"status": 202, "body": {"applied": False, "reason": "unknown_customer"}}

    canonical, error = normalize_plan(payload.get("plan"), link.mapping_version)
    if error:
        _attempt(db, connector, link, event_id, revision, "mapping_rejected", {"reason": error})
        db.commit()
        return {"status": 202, "body": {"applied": False, "reason": "mapping"}}

    dest_connector = db.get(ConnectorConfig, link.destination_connector_id)
    client = ServiceClient(settings.cir_support_base_url, settings.support_token, timeout=3.0)
    from recovery_api.clients import support_account

    status, dest = support_account(client, link.destination_customer_id)
    if status == 429:
        count = _attempt(db, connector, link, event_id, revision, "rate_limited", {"http_status": 429})
        if count < 3:
            enqueue(
                db,
                workspace_id=connector.workspace_id,
                kind="sync_retry",
                payload={"connector_id": str(connector.id), "event_id": event_id},
                delay_seconds=2,
            )
        db.commit()
        return {"status": 202, "body": {"applied": False, "reason": "rate_limited"}}
    if status != 200:
        _attempt(db, connector, link, event_id, revision, "failed", {"http_status": status})
        db.commit()
        return {"status": 202, "body": {"applied": False, "reason": "destination_unavailable"}}
    applied_rev = dest.get("source_revision_applied")
    if isinstance(revision, int) and isinstance(applied_rev, int) and revision < applied_rev:
        _attempt(db, connector, link, event_id, revision, "rejected_stale", {"applied_revision": applied_rev})
        db.commit()
        return {"status": 200, "body": {"applied": False, "reason": "out_of_order"}}
    if dest.get("plan") == canonical and applied_rev == revision:
        _attempt(db, connector, link, event_id, revision, "rejected_duplicate", {"already_applied": True})
        db.commit()
        return {"status": 200, "body": {"applied": False, "reason": "already_applied"}}
    write_status, write_body = support_patch_plan(
        client,
        link.destination_customer_id,
        plan=canonical,
        expected_revision=dest["revision"],
        source_revision=revision if isinstance(revision, int) else None,
        idempotency_key=f"event-{event_id}",
    )
    if write_status == 200:
        _attempt(db, connector, link, event_id, revision, "applied", {"revision": write_body.get("revision")})
        audit(
            db,
            workspace_id=connector.workspace_id,
            action="sync_applied",
            resource_type="sync_event",
            resource_id=event_id,
            metadata={"plan": canonical},
        )
        db.commit()
        return {"status": 200, "body": {"applied": True}}
    if write_status == 409:
        _attempt(db, connector, link, event_id, revision, "rejected_stale", {"error": write_body.get("error")})
        db.commit()
        return {"status": 200, "body": {"applied": False, "reason": "conflict"}}
    _attempt(db, connector, link, event_id, revision, "failed", {"http_status": write_status})
    db.commit()
    return {"status": 202, "body": {"applied": False, "reason": "not_applied"}}


def _attempt(db: Session, connector, link, event_id: str, revision, state: str, response: dict) -> int:
    previous = 0
    if link is not None:
        earlier = db.scalars(
            select(SyncAttempt).where(
                SyncAttempt.workspace_id == connector.workspace_id,
                SyncAttempt.customer_link_id == link.id,
                SyncAttempt.external_event_id == event_id,
            )
        ).all()
        previous = len(earlier)
    count = previous + 1
    db.add(
        SyncAttempt(
            workspace_id=connector.workspace_id,
            customer_link_id=link.id if link else None,
            external_event_id=event_id,
            source_revision=revision if isinstance(revision, int) else None,
            state=state,
            attempt_count=count,
            sanitized_response=response,
            created_at=utcnow(),
        )
    )
    return count


def reconcile_workspace(db: Session, workspace_id) -> int:
    from recovery_api.models import Incident, Workspace

    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        return 0
    opened = 0
    links = db.scalars(select(CustomerLink).where(CustomerLink.workspace_id == workspace_id)).all()
    for link in links:
        connector = db.get(ConnectorConfig, link.source_connector_id)
        if connector and connector.provider == "hubspot" and not get_settings().hubspot_token:
            continue
        from recovery_api.records import read_crm, read_support

        source = read_crm(db, link, investigation_id=None)
        dest = read_support(db, link, investigation_id=None)
        if source.get("status") != "ok" or dest.get("status") != "ok":
            continue
        if source.get("plan") == dest.get("plan"):
            continue
        existing = db.scalar(
            select(Incident).where(
                Incident.workspace_id == workspace_id,
                Incident.customer_link_id == link.id,
                Incident.state.in_(
                    ["OPEN", "INVESTIGATING", "NEEDS_INFORMATION", "AWAITING_APPROVAL", "EXECUTING", "VERIFYING"]
                ),
            )
        )
        if existing:
            continue
        db.add(
            Incident(
                workspace_id=workspace_id,
                customer_link_id=link.id,
                title=f"{link.display_name} plan differs",
                description=(
                    f"Reconciliation read {source.get('plan')} from the CRM and {dest.get('plan')} from support. "
                    "This is a synthetic record."
                    if workspace.synthetic
                    else f"Reconciliation read {source.get('plan')} from the external CRM and {dest.get('plan')} from the demo support service."
                ),
                state="OPEN",
                severity="medium",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        opened += 1
    audit(db, workspace_id=workspace_id, action="reconciliation_completed", resource_type="workspace", resource_id=str(workspace_id), metadata={"opened": opened})
    db.commit()
    return opened
