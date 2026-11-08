"""Add a synthetic verification workspace. Does not reset or delete demo records."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "packages" / "recovery_core")]

from sqlalchemy import create_engine, select, text

from recovery_api.db import session_factory
from recovery_api.models import ConnectorConfig, CustomerLink, Incident, Membership, User, Workspace
from recovery_api.security import utcnow

OUT = ROOT / "var" / "round3-verify.json"
CRM_URL = "postgresql+psycopg://crm_app:crm_app_local@127.0.0.1:55438/crm"
SUPPORT_URL = "postgresql+psycopg://support_app:support_app_local@127.0.0.1:55438/support"


def _ensure_simulator_rows() -> None:
    now = utcnow()
    crm = create_engine(CRM_URL)
    with crm.begin() as conn:
        if conn.execute(text("SELECT 1 FROM customers WHERE id = 'cus_quinn'")).first() is None:
            conn.execute(
                text(
                    """
                    INSERT INTO customers (id, display_name, plan, revision, notes, updated_at)
                    VALUES ('cus_quinn', 'Quinn Adler', 'premium', 4, '', :now)
                    """
                ),
                {"now": now},
            )
    support = create_engine(SUPPORT_URL)
    with support.begin() as conn:
        if conn.execute(text("SELECT 1 FROM accounts WHERE id = 'sup_quinn'")).first() is None:
            conn.execute(
                text(
                    """
                    INSERT INTO accounts
                      (id, display_name, plan, revision, source_revision_applied, write_count, updated_at)
                    VALUES ('sup_quinn', 'Quinn Adler', 'basic', 2, 1, 0, :now)
                    """
                ),
                {"now": now},
            )


def main() -> None:
    _ensure_simulator_rows()
    db = session_factory()()
    try:
        workspace = db.scalar(select(Workspace).where(Workspace.slug == "round3-verify"))
        if workspace is None:
            workspace = Workspace(name="Round3 Verify", slug="round3-verify", synthetic=True, created_at=utcnow())
            db.add(workspace)
            db.flush()
        for username, roles in (("northstar.operator", ["operator"]), ("northstar.approver", ["approver"])):
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                raise SystemExit(f"missing user {username}")
            existing = db.scalar(
                select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == workspace.id)
            )
            if existing is None:
                db.add(Membership(user_id=user.id, workspace_id=workspace.id, roles=roles, created_at=utcnow()))
        crm = db.scalar(
            select(ConnectorConfig).where(
                ConnectorConfig.workspace_id == workspace.id, ConnectorConfig.provider == "crm_simulator"
            )
        )
        support = db.scalar(
            select(ConnectorConfig).where(
                ConnectorConfig.workspace_id == workspace.id, ConnectorConfig.provider == "support_service"
            )
        )
        if crm is None:
            crm = ConnectorConfig(
                workspace_id=workspace.id,
                name="Synthetic CRM",
                provider="crm_simulator",
                secret_configured=True,
                settings={"mapping_version": "v1"},
                capabilities={"read": True, "write": False},
                created_at=utcnow(),
            )
            db.add(crm)
        if support is None:
            support = ConnectorConfig(
                workspace_id=workspace.id,
                name="Synthetic support",
                provider="support_service",
                secret_configured=True,
                settings={"mapping_version": "v1"},
                capabilities={"read": True, "write": True},
                created_at=utcnow(),
            )
            db.add(support)
        db.flush()
        link = db.scalar(
            select(CustomerLink).where(
                CustomerLink.workspace_id == workspace.id, CustomerLink.source_customer_id == "cus_quinn"
            )
        )
        if link is None:
            link = CustomerLink(
                workspace_id=workspace.id,
                source_connector_id=crm.id,
                source_customer_id="cus_quinn",
                destination_connector_id=support.id,
                destination_customer_id="sup_quinn",
                display_name="Quinn Adler",
                mapping_version="v1",
                created_at=utcnow(),
            )
            db.add(link)
            db.flush()
        incident = db.scalar(
            select(Incident).where(Incident.workspace_id == workspace.id, Incident.customer_link_id == link.id, Incident.state == "OPEN")
        )
        if incident is None:
            incident = Incident(
                workspace_id=workspace.id,
                customer_link_id=link.id,
                title="Quinn Adler plan mismatch",
                description="Synthetic verification account. CRM plan is premium and support is still basic.",
                severity="medium",
                state="OPEN",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.add(incident)
            db.flush()
        db.commit()
        OUT.write_text(
            json.dumps(
                {
                    "workspace_id": str(workspace.id),
                    "incident_id": str(incident.id),
                    "customer_link_id": str(link.id),
                    "account_id": "sup_quinn",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"prepared workspace round3-verify incident {incident.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
