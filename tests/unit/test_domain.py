from datetime import datetime, timedelta, timezone

from recovery_core.backoff import retry_delay_seconds
from recovery_core.diagnosis import diagnose
from recovery_core.hashing import canonical_hash
from recovery_core.mapping import normalize_plan
from recovery_core.proposals import build_repair_proposal, hash_proposal_fields, isoformat_utc
from recovery_core.redact import redact_text, sanitize
from recovery_core.signing import signatures_match, sign_body


def _obs(**overrides):
    base = {
        "has_link": True,
        "source": {
            "status": "ok",
            "plan": "premium",
            "revision": 4,
            "fingerprint": "src",
            "freshness": "established",
            "evidence_id": "e1",
        },
        "destination": {
            "status": "ok",
            "plan": "basic",
            "revision": 2,
            "source_revision_applied": 1,
            "evidence_id": "e2",
        },
        "attempts": [],
        "runbook_evidence_ids": [],
    }
    base.update(overrides)
    return base


def test_mapping_versions_and_unknown_values():
    assert normalize_plan("Pro") == ("premium", None)
    assert normalize_plan("gold")[1] == "unmapped"
    assert normalize_plan(None)[1] == "missing_field"
    assert normalize_plan("basic", "v9")[1] == "unknown_mapping_version"


def test_hash_is_stable_and_changes_when_content_changes():
    assert canonical_hash({"b": 1, "a": 2}) == canonical_hash({"a": 2, "b": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_redaction_removes_bearer_material():
    assert "secret-value" not in redact_text("Authorization: Bearer secret-value")
    cleaned = sanitize({"token": "abc", "plan": "basic", "notes": "password=hunter2"})
    assert cleaned["token"] == "[redacted]"
    assert "hunter2" not in cleaned["notes"]


def test_signature_round_trip():
    body = b'{"event_id":"evt_1"}'
    header = sign_body("webhook-local-dev", body)
    assert signatures_match("webhook-local-dev", body, header)
    assert not signatures_match("webhook-local-dev", body, "sha256=nope")


def test_retry_delay_honors_positive_header_and_avoids_a_zero_wait():
    assert retry_delay_seconds("2", attempt=1) == 2
    assert retry_delay_seconds("0", attempt=1) == 0.5
    assert retry_delay_seconds(None, attempt=2) == 1


def test_missing_update_proposes_the_source_plan_only():
    diagnosis = diagnose(_obs())
    assert diagnosis["cause"] == "missing_update"
    assert diagnosis["repair_allowed"] is True
    proposal = build_repair_proposal(
        workspace_id="w",
        incident_id="i",
        customer_link_id="c",
        destination_connector_id="d",
        diagnosis=diagnosis,
        source=_obs()["source"],
        destination=_obs()["destination"],
    )
    assert proposal["field"] == "plan"
    assert proposal["before"] == "basic"
    assert proposal["after"] == "premium"
    assert proposal["proposal_hash"] == hash_proposal_fields(
        workspace_id="w",
        incident_id="i",
        customer_link_id="c",
        connector_id="d",
        mapping_version="v1",
        before="basic",
        after="premium",
        source_preconditions=proposal["source_preconditions"],
        destination_preconditions=proposal["destination_preconditions"],
        evidence_ids=proposal["evidence_ids"],
        expires_at=proposal["expires_at"],
    )


def test_injection_text_does_not_change_the_repair_value():
    diagnosis = diagnose(_obs(untrusted_text_present=True))
    proposal = build_repair_proposal(
        workspace_id="w",
        incident_id="i",
        customer_link_id="c",
        destination_connector_id="d",
        diagnosis=diagnosis,
        source=_obs()["source"],
        destination=_obs()["destination"],
    )
    assert proposal["after"] == "premium"
    assert "instruction-like" in " ".join(diagnosis["limitations"])


def test_access_mapping_and_healthy_cases_do_not_propose_repairs():
    denied = diagnose(_obs(source={"status": "unauthorized", "evidence_id": "e1"}))
    assert denied["cause"] == "access_missing" and denied["repair_allowed"] is False
    unmapped = diagnose(_obs(source={"status": "unmapped", "evidence_id": "e1"}))
    assert unmapped["next_action"] == "correct_mapping"
    healthy = diagnose(_obs(destination={**_obs()["destination"], "plan": "premium", "source_revision_applied": 4}))
    assert healthy["cause"] == "no_mismatch"
    assert build_repair_proposal(
        workspace_id="w",
        incident_id="i",
        customer_link_id="c",
        destination_connector_id="d",
        diagnosis=healthy,
        source=_obs()["source"],
        destination={**_obs()["destination"], "plan": "premium"},
    ) is None


def test_expired_hash_inputs_include_the_expiry():
    moment = datetime(2026, 11, 2, tzinfo=timezone.utc)
    first = build_repair_proposal(
        workspace_id="w",
        incident_id="i",
        customer_link_id="c",
        destination_connector_id="d",
        diagnosis=diagnose(_obs()),
        source=_obs()["source"],
        destination=_obs()["destination"],
        now=moment,
        ttl_minutes=30,
    )
    later = build_repair_proposal(
        workspace_id="w",
        incident_id="i",
        customer_link_id="c",
        destination_connector_id="d",
        diagnosis=diagnose(_obs()),
        source=_obs()["source"],
        destination=_obs()["destination"],
        now=moment + timedelta(minutes=5),
        ttl_minutes=30,
    )
    assert first["expires_at"] != later["expires_at"]
    assert first["proposal_hash"] != later["proposal_hash"]
    assert isoformat_utc(moment) == "2026-11-02T00:00:00+00:00"


def test_contradictory_and_duplicate_and_unknown_customer():
    contradictory = diagnose(
        _obs(destination={**_obs()["destination"], "plan": "basic", "source_revision_applied": 4})
    )
    assert contradictory["cause"] == "contradictory_evidence"
    duplicate = diagnose(
        _obs(
            destination={**_obs()["destination"], "plan": "premium", "source_revision_applied": 4},
            attempts=[{"state": "rejected_duplicate", "attempt_count": 2, "evidence_id": "e3"}],
        )
    )
    assert duplicate["cause"] == "duplicate_event"
    unknown = diagnose({"has_link": False})
    assert unknown["cause"] == "ambiguous_identity"
