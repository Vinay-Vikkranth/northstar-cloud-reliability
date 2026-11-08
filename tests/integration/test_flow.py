import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from recovery_api.db import session_factory
from recovery_api.demo import apply_scenario
from recovery_api.main import app
from recovery_api.models import CustomerLink, Membership, User, WorkerJob, Workspace
from recovery_api.security import hash_password, utcnow
from recovery_worker.main import process_available_jobs

pytestmark = pytest.mark.integration


def _client(username: str, password: str, workspace_id: str) -> tuple[TestClient, str]:
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["csrf_token"]
    client.headers.update({"X-CSRF-Token": token, "X-Workspace-Id": workspace_id})
    return client, token


def test_missing_update_is_verified_over_http(services):
    db = session_factory()()
    password = "operator-pass-123"
    approver_password = "approver-pass-123"
    try:
        workspace = db.scalar(select(Workspace).where(Workspace.slug == "northstar-demo"))
        if workspace is None:
            workspace = Workspace(name="Northstar Demo", slug="northstar-demo", synthetic=True, created_at=utcnow())
            db.add(workspace)
            db.flush()
        operator = User(username=f"op-{uuid.uuid4().hex[:8]}", password_hash=hash_password(password), created_at=utcnow())
        approver = User(username=f"ap-{uuid.uuid4().hex[:8]}", password_hash=hash_password(approver_password), created_at=utcnow())
        db.add_all([operator, approver])
        db.flush()
        db.add(Membership(user_id=operator.id, workspace_id=workspace.id, roles=["operator", "administrator"], created_at=utcnow()))
        db.add(Membership(user_id=approver.id, workspace_id=workspace.id, roles=["approver"], created_at=utcnow()))
        from recovery_api.models import ConnectorConfig

        crm = db.scalar(
            select(ConnectorConfig).where(ConnectorConfig.workspace_id == workspace.id, ConnectorConfig.provider == "crm_simulator")
        )
        support = db.scalar(
            select(ConnectorConfig).where(ConnectorConfig.workspace_id == workspace.id, ConnectorConfig.provider == "support_service")
        )
        if crm is None or support is None:
            crm = crm or ConnectorConfig(
                workspace_id=workspace.id,
                name="crm",
                provider="crm_simulator",
                secret_configured=True,
                settings={"mapping_version": "v1"},
                capabilities={"read": True, "write": False},
                created_at=utcnow(),
            )
            support = support or ConnectorConfig(
                workspace_id=workspace.id,
                name="support",
                provider="support_service",
                secret_configured=True,
                settings={"mapping_version": "v1"},
                capabilities={"read": True, "write": True},
                created_at=utcnow(),
            )
            db.add_all([row for row in (crm, support) if row.id is None])
            db.flush()
        link = db.scalar(
            select(CustomerLink).where(
                CustomerLink.workspace_id == workspace.id,
                CustomerLink.source_customer_id == "cus_alex",
            )
        )
        if link is None:
            db.add(
                CustomerLink(
                    workspace_id=workspace.id,
                    source_connector_id=crm.id,
                    source_customer_id="cus_alex",
                    destination_connector_id=support.id,
                    destination_customer_id="sup_alex",
                    display_name="Alex Rivera",
                    mapping_version="v1",
                    created_at=utcnow(),
                )
            )
        db.commit()
        apply_scenario(db, workspace, "missing_update")
        workspace_id = str(workspace.id)
        operator_name = operator.username
        approver_name = approver.username
    finally:
        db.close()

    operator_client, _csrf = _client(operator_name, password, workspace_id)
    listed = operator_client.get("/api/v1/incidents")
    assert listed.status_code == 200, listed.text
    open_items = [item for item in listed.json()["items"] if item["state"] == "OPEN"]
    incident_id = open_items[0]["id"]
    started = operator_client.post(f"/api/v1/incidents/{incident_id}/investigate")
    assert started.status_code == 200, started.text
    assert process_available_jobs() >= 1
    detail = operator_client.get(f"/api/v1/incidents/{incident_id}")
    body = detail.json()
    assert body["diagnosis"]["cause"] == "missing_update", body
    proposal = body["proposal"]
    assert proposal["before"] == "basic" and proposal["after"] == "premium"

    approver_client, _csrf = _client(approver_name, approver_password, workspace_id)
    approved = approver_client.post(
        f"/api/v1/proposals/{proposal['id']}/approve",
        json={"proposal_hash": proposal["proposal_hash"], "reason": "Reviewed the CRM revision and the support account."},
    )
    assert approved.status_code == 200, approved.text
    assert process_available_jobs() >= 1
    final = operator_client.get(f"/api/v1/incidents/{incident_id}").json()
    assert final["state"] == "RESOLVED", final
    assert final["verification"]["outcome"] == "verified"
    from recovery_api.settings import get_settings

    account = httpx.get(
        get_settings().cir_support_base_url + "/v1/accounts/sup_alex",
        headers={"Authorization": "Bearer support-local"},
        timeout=5,
    )
    assert account.status_code == 200
    assert account.json()["plan"] == "premium"
    assert account.json()["write_count"] == 1
    db = session_factory()()
    try:
        failed = db.scalars(select(WorkerJob).where(WorkerJob.state == "dead")).all()
        assert failed == []
    finally:
        db.close()
