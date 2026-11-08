from datetime import datetime, timedelta, timezone
from typing import Any

from recovery_core import MAPPING_VERSION, WRITABLE_FIELDS
from recovery_core.hashing import canonical_hash


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def hash_proposal_fields(
    *,
    workspace_id: str,
    incident_id: str,
    customer_link_id: str,
    connector_id: str,
    mapping_version: str,
    before: str,
    after: str,
    source_preconditions: dict,
    destination_preconditions: dict,
    evidence_ids: list[str],
    expires_at: str,
    version: int = 1,
) -> str:
    body = {
        "workspace_id": workspace_id,
        "incident_id": incident_id,
        "customer_link_id": customer_link_id,
        "connector_id": connector_id,
        "mapping_version": mapping_version,
        "field": "plan",
        "before": before,
        "after": after,
        "source_preconditions": source_preconditions,
        "destination_preconditions": destination_preconditions,
        "evidence_ids": evidence_ids,
        "expires_at": expires_at,
        "version": version,
    }
    return canonical_hash(body)


def build_repair_proposal(
    *,
    workspace_id: str,
    incident_id: str,
    customer_link_id: str,
    destination_connector_id: str,
    diagnosis: dict[str, Any],
    source: dict[str, Any],
    destination: dict[str, Any],
    mapping_version: str = MAPPING_VERSION,
    now: datetime | None = None,
    ttl_minutes: int = 30,
) -> dict[str, Any] | None:
    """Build a plan-only proposal from authoritative reads. Model text cannot supply the write."""
    if not diagnosis.get("repair_allowed"):
        return None
    if diagnosis.get("next_action") != "propose_repair":
        return None
    if source.get("status") != "ok" or destination.get("status") != "ok":
        return None
    if source.get("freshness") == "unestablished":
        return None
    after = source.get("plan")
    before = destination.get("plan")
    if not isinstance(after, str) or not isinstance(before, str) or after == before:
        return None
    if "plan" not in WRITABLE_FIELDS:
        return None
    moment = now or datetime.now(timezone.utc)
    expires = (moment + timedelta(minutes=ttl_minutes)).astimezone(timezone.utc).replace(microsecond=0)
    evidence_ids = sorted(str(item) for item in diagnosis.get("evidence_ids") or [])
    source_preconditions = {
        "revision": source.get("revision"),
        "plan": after,
        "fingerprint": source.get("fingerprint"),
    }
    destination_preconditions = {"revision": destination.get("revision"), "plan": before}
    expires_at = isoformat_utc(expires)
    proposal_hash = hash_proposal_fields(
        workspace_id=workspace_id,
        incident_id=incident_id,
        customer_link_id=customer_link_id,
        connector_id=destination_connector_id,
        mapping_version=mapping_version,
        before=before,
        after=after,
        source_preconditions=source_preconditions,
        destination_preconditions=destination_preconditions,
        evidence_ids=evidence_ids,
        expires_at=expires_at,
    )
    return {
        "workspace_id": workspace_id,
        "incident_id": incident_id,
        "customer_link_id": customer_link_id,
        "connector_id": destination_connector_id,
        "mapping_version": mapping_version,
        "field": "plan",
        "before": before,
        "after": after,
        "source_preconditions": source_preconditions,
        "destination_preconditions": destination_preconditions,
        "evidence_ids": evidence_ids,
        "expires_at": expires_at,
        "version": 1,
        "proposal_hash": proposal_hash,
    }
