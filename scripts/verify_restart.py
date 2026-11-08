"""Check cancellation and idempotent replay against the restarted local API."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "packages" / "recovery_core")]

import httpx
from sqlalchemy import select

from recovery_api.db import session_factory
from recovery_api.models import InvestigationRun, Proposal, RepairIntent

CREDS = json.loads((ROOT / "var" / "local-credentials.json").read_text(encoding="utf-8"))
META = json.loads((ROOT / "var" / "round3-verify.json").read_text(encoding="utf-8"))


def _session(username: str, password: str) -> tuple[httpx.Client, dict]:
    client = httpx.Client(base_url="http://127.0.0.1:28601", timeout=15)
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    headers = {"X-CSRF-Token": response.json()["csrf_token"], "X-Workspace-Id": META["workspace_id"]}
    return client, headers


def _account() -> dict:
    response = httpx.get(
        "http://127.0.0.1:28621/v1/accounts/sup_quinn",
        headers={"Authorization": "Bearer support-local"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def replay_approved_write() -> None:
    before = _account()
    db = session_factory()()
    try:
        proposal = db.scalar(
            select(Proposal)
            .where(Proposal.incident_id == META["incident_id"], Proposal.decision_state == "approved")
            .order_by(Proposal.created_at.desc())
        )
        if proposal is None:
            raise SystemExit("no approved proposal on the verification incident")
        intent = db.scalar(select(RepairIntent).where(RepairIntent.proposal_id == proposal.id))
        if intent is None:
            raise SystemExit("approved proposal has no repair intent")
        key = intent.idempotency_key
        payload = dict(intent.payload)
        proposal_id = str(proposal.id)
        proposal_hash = proposal.proposal_hash
    finally:
        db.close()
    approver, headers = _session(CREDS["users"]["approver"], CREDS["passwords"]["approver"])
    try:
        again = approver.post(
            f"/api/v1/proposals/{proposal_id}/approve",
            headers=headers,
            json={"proposal_hash": proposal_hash, "reason": "Replay of an approval that already completed."},
        )
        if again.status_code != 409:
            raise SystemExit(f"second approval returned {again.status_code}: {again.text}")
    finally:
        approver.close()
    replay = httpx.patch(
        "http://127.0.0.1:28621/v1/accounts/sup_quinn",
        headers={
            "Authorization": "Bearer support-local",
            "Idempotency-Key": key,
            "If-Match": str(payload["expected_revision"]),
        },
        json=payload,
        timeout=10,
    )
    if replay.status_code != 200:
        raise SystemExit(f"idempotent replay returned {replay.status_code}: {replay.text}")
    after = _account()
    if after["write_count"] != before["write_count"] or after["revision"] != before["revision"]:
        raise SystemExit(f"replay changed the account from {before} to {after}")
    if after["plan"] != "premium" or after["write_count"] != 1:
        raise SystemExit(f"unexpected account after the verified repair: {after}")
    print(f"replay_ok write_count={after['write_count']} revision={after['revision']} second_approval=409")


def cancel_before_start() -> None:
    client, headers = _session(CREDS["users"]["operator"], CREDS["passwords"]["operator"])
    try:
        for attempt in range(1, 4):
            created = client.post(
                "/api/v1/incidents",
                headers=headers,
                json={
                    "title": f"Cancel queued run {attempt}",
                    "description": "This investigation is cancelled before its job runs.",
                    "severity": "low",
                    "customer_link_id": META["customer_link_id"],
                },
            )
            created.raise_for_status()
            incident_id = created.json()["id"]
            started = client.post(f"/api/v1/incidents/{incident_id}/investigate", headers=headers)
            started.raise_for_status()
            investigation_id = started.json()["investigation_id"]
            cancelled = client.post(f"/api/v1/incidents/{incident_id}/cancel", headers=headers)
            cancelled.raise_for_status()
            detail = client.get(f"/api/v1/incidents/{incident_id}", headers=headers)
            detail.raise_for_status()
            body = detail.json()
            db = session_factory()()
            try:
                run = db.get(InvestigationRun, investigation_id)
                state = run.state if run is not None else None
            finally:
                db.close()
            if body["state"] == "OPEN" and state == "CANCELLED" and (body["proposal"] is None or body["proposal"]["decision_state"] != "open"):
                print(f"cancel_before_start_ok attempt={attempt} incident={incident_id}")
                return
            print(f"cancel_attempt_{attempt} incident_state={body['state']} run_state={state}")
    finally:
        client.close()
    raise SystemExit("queued investigation was not cancelled before it started")


if __name__ == "__main__":
    replay_approved_write()
    cancel_before_start()
