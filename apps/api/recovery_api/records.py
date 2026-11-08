from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from recovery_api.clients import ServiceClient, crm_customer, support_account
from recovery_api.models import AuditEvent, ConnectorConfig, CustomerLink, Evidence
from recovery_api.settings import get_settings
from recovery_core.hashing import canonical_hash, fingerprint_record
from recovery_core.mapping import normalize_plan
from recovery_core.redact import sanitize


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def audit(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    workspace_id=None,
    actor_id=None,
    resource_version: str | None = None,
    metadata: dict | None = None,
    trace_id: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_id=actor_id,
            workspace_id=workspace_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            resource_version=resource_version,
            trace_id=trace_id,
            metadata_json=sanitize(metadata or {}),
            created_at=utcnow(),
        )
    )


def add_evidence(
    db: Session,
    *,
    workspace_id,
    investigation_run_id,
    source: str,
    content: dict,
    revision: str | None = None,
    sensitivity: str = "synthetic",
) -> Evidence:
    cleaned = sanitize(content)
    row = Evidence(
        workspace_id=workspace_id,
        investigation_run_id=investigation_run_id,
        source=source,
        observed_at=utcnow(),
        sanitized_content=cleaned,
        content_fingerprint=canonical_hash(cleaned),
        revision=revision,
        sensitivity=sensitivity,
    )
    db.add(row)
    db.flush()
    return row


def link_in_workspace(db: Session, workspace_id, link_id) -> CustomerLink | None:
    if link_id is None:
        return None
    return db.scalar(select(CustomerLink).where(CustomerLink.id == link_id, CustomerLink.workspace_id == workspace_id))


def connector_client(connector: ConnectorConfig) -> ServiceClient:
    settings = get_settings()
    if connector.provider == "crm_simulator":
        return ServiceClient(settings.cir_crm_base_url, settings.crm_read_token)
    if connector.provider == "support_service":
        return ServiceClient(settings.cir_support_base_url, settings.support_token)
    raise ValueError("connector provider is not an HTTP demo service")


def interpret_status(status: int) -> str:
    if status == 401:
        return "unauthorized"
    if status == 429:
        return "rate_limited"
    if status in {404}:
        return "malformed"
    if status >= 500 or status in {598, 599}:
        return "unavailable" if status != 599 else "timeout"
    if status != 200:
        return "malformed"
    return "ok"


def read_crm(db: Session, link: CustomerLink, *, investigation_id) -> dict[str, Any]:
    connector = db.get(ConnectorConfig, link.source_connector_id)
    if connector is None or connector.workspace_id != link.workspace_id:
        return {"status": "malformed", "evidence_id": None}
    if connector.provider == "hubspot":
        from recovery_api.hubspot import HubSpotClient, HubSpotError

        settings = get_settings()
        plan_property = (connector.settings or {}).get("plan_property") or settings.hubspot_plan_property
        client = HubSpotClient(settings.hubspot_token, plan_property=plan_property)
        try:
            observed = client.get_contact(link.source_customer_id)
        except HubSpotError as exc:
            status = {
                "unauthorized": "unauthorized",
                "rate_limited": "rate_limited",
                "not_found": "malformed",
                "malformed": "malformed",
            }.get(exc.code, "unavailable")
            evidence = add_evidence(
                db,
                workspace_id=link.workspace_id,
                investigation_run_id=investigation_id,
                source="crm",
                content={"status": status, "http_status": exc.status},
            )
            return {"status": status, "http_status": exc.status, "evidence_id": str(evidence.id), "provider": "hubspot", "freshness": "unestablished"}
        finally:
            client.close()
        evidence = add_evidence(
            db,
            workspace_id=link.workspace_id,
            investigation_run_id=investigation_id,
            source="crm",
            content={key: observed.get(key) for key in ("id", "status", "plan", "raw_plan", "modified_at", "freshness")},
            revision=str(observed.get("modified_at") or ""),
        )
        observed["evidence_id"] = str(evidence.id)
        return observed

    status, body = crm_customer(connector_client(connector), link.source_customer_id)
    kind = interpret_status(status)
    if kind != "ok":
        evidence = add_evidence(
            db,
            workspace_id=link.workspace_id,
            investigation_run_id=investigation_id,
            source="crm",
            content={"status": kind, "http_status": status},
        )
        return {"status": kind, "http_status": status, "evidence_id": str(evidence.id), "provider": connector.provider, "freshness": "unestablished"}
    raw_plan = body.get("plan") if "plan" in body else None
    missing = "plan" not in body
    canonical, error = (None, "missing_field") if missing else normalize_plan(raw_plan, link.mapping_version)
    record_status = "ok"
    if error == "missing_field":
        record_status = "missing_field"
    elif error:
        record_status = "unmapped"
    modified = body.get("updated_at")
    revision = body.get("revision")
    freshness = "established" if isinstance(revision, int) and modified else "unestablished"
    evidence = add_evidence(
        db,
        workspace_id=link.workspace_id,
        investigation_run_id=investigation_id,
        source="crm",
        content={
            "id": body.get("id"),
            "status": record_status,
            "plan": canonical,
            "raw_plan": raw_plan,
            "revision": revision,
            "updated_at": modified,
            "notes": body.get("notes"),
            "content_trust": "untrusted",
        },
        revision=str(revision) if revision is not None else None,
    )
    return {
        "provider": connector.provider,
        "status": record_status,
        "http_status": status,
        "id": body.get("id"),
        "plan": canonical,
        "raw_plan": raw_plan,
        "revision": revision if isinstance(revision, int) else None,
        "modified_at": modified,
        "notes": body.get("notes"),
        "fingerprint": fingerprint_record(
            record_id=str(body.get("id")),
            plan=canonical,
            revision=revision,
            modified_at=modified if isinstance(modified, str) else None,
        ),
        "freshness": freshness if record_status == "ok" else "unestablished",
        "evidence_id": str(evidence.id),
    }


def read_support(db: Session, link: CustomerLink, *, investigation_id, source_name: str = "support") -> dict[str, Any]:
    connector = db.get(ConnectorConfig, link.destination_connector_id)
    if connector is None or connector.workspace_id != link.workspace_id or connector.provider != "support_service":
        return {"status": "malformed", "evidence_id": None, "freshness": "unestablished"}
    status, body = support_account(connector_client(connector), link.destination_customer_id)
    kind = interpret_status(status)
    if kind != "ok":
        evidence = add_evidence(
            db,
            workspace_id=link.workspace_id,
            investigation_run_id=investigation_id,
            source=source_name,
            content={"status": kind, "http_status": status},
        )
        return {"status": kind, "http_status": status, "evidence_id": str(evidence.id), "freshness": "unestablished"}
    plan, error = normalize_plan(body.get("plan"), link.mapping_version)
    record_status = "ok" if error is None else "malformed"
    evidence = add_evidence(
        db,
        workspace_id=link.workspace_id,
        investigation_run_id=investigation_id,
        source=source_name,
        content={
            "id": body.get("id"),
            "status": record_status,
            "plan": plan,
            "revision": body.get("revision"),
            "source_revision_applied": body.get("source_revision_applied"),
            "write_count": body.get("write_count"),
            "updated_at": body.get("updated_at"),
        },
        revision=str(body.get("revision")),
    )
    return {
        "status": record_status,
        "http_status": status,
        "id": body.get("id"),
        "plan": plan,
        "revision": body.get("revision") if isinstance(body.get("revision"), int) else None,
        "source_revision_applied": body.get("source_revision_applied"),
        "write_count": body.get("write_count"),
        "modified_at": body.get("updated_at"),
        "fingerprint": fingerprint_record(
            record_id=str(body.get("id")),
            plan=plan,
            revision=body.get("revision"),
            modified_at=body.get("updated_at") if isinstance(body.get("updated_at"), str) else None,
        ),
        "freshness": "established" if isinstance(body.get("revision"), int) else "unestablished",
        "evidence_id": str(evidence.id),
    }
