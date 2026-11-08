"""Create two synthetic workspaces and local users. Passwords are written only under var/."""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "packages" / "recovery_core"), str(ROOT / "scripts")]

from sqlalchemy import select

from load_runbooks import load
from recovery_api.db import session_factory
from recovery_api.demo import CUSTOMERS, apply_scenario
from recovery_api.models import ConnectorConfig, CustomerLink, EvaluationReport, Membership, User, Workspace
from recovery_api.security import hash_password, utcnow

CREDENTIALS = ROOT / "var" / "local-credentials.json"


def _user(db, username: str, password: str) -> User:
    row = db.scalar(select(User).where(User.username == username))
    if row:
        return row
    row = User(username=username, password_hash=hash_password(password), created_at=utcnow())
    db.add(row)
    db.flush()
    return row


def _workspace(db, name: str, slug: str) -> Workspace:
    row = db.scalar(select(Workspace).where(Workspace.slug == slug))
    if row:
        return row
    row = Workspace(name=name, slug=slug, synthetic=True, created_at=utcnow())
    db.add(row)
    db.flush()
    return row


def _membership(db, user: User, workspace: Workspace, roles: list[str]) -> None:
    row = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == workspace.id))
    if row is None:
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, roles=roles, created_at=utcnow()))


def _connectors(db, workspace: Workspace) -> tuple[ConnectorConfig, ConnectorConfig]:
    crm = db.scalar(select(ConnectorConfig).where(ConnectorConfig.workspace_id == workspace.id, ConnectorConfig.provider == "crm_simulator"))
    support = db.scalar(
        select(ConnectorConfig).where(ConnectorConfig.workspace_id == workspace.id, ConnectorConfig.provider == "support_service")
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
    return crm, support


def _links(db, workspace: Workspace, crm: ConnectorConfig, support: ConnectorConfig) -> None:
    for source_id, dest_id, name in list(CUSTOMERS.values()):
        existing = db.scalar(
            select(CustomerLink).where(CustomerLink.workspace_id == workspace.id, CustomerLink.source_customer_id == source_id)
        )
        if existing:
            continue
        db.add(
            CustomerLink(
                workspace_id=workspace.id,
                source_connector_id=crm.id,
                source_customer_id=source_id,
                destination_connector_id=support.id,
                destination_customer_id=dest_id,
                display_name=name,
                mapping_version="v1",
                created_at=utcnow(),
            )
        )


def main() -> None:
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    if CREDENTIALS.exists():
        passwords = json.loads(CREDENTIALS.read_text(encoding="utf-8"))["passwords"]
    else:
        passwords = {name: secrets.token_urlsafe(18) for name in ("operator", "approver", "viewer", "admin", "combined", "harbor")}
    db = session_factory()()
    try:
        north = _workspace(db, "Northstar Demo", "northstar-demo")
        harbor = _workspace(db, "Harbor Isolation", "harbor-isolation")
        operator = _user(db, "northstar.operator", passwords["operator"])
        approver = _user(db, "northstar.approver", passwords["approver"])
        viewer = _user(db, "northstar.viewer", passwords["viewer"])
        admin = _user(db, "northstar.admin", passwords["admin"])
        combined = _user(db, "northstar.combined", passwords["combined"])
        harbor_operator = _user(db, "harbor.operator", passwords["harbor"])
        _membership(db, operator, north, ["operator"])
        _membership(db, approver, north, ["approver"])
        _membership(db, viewer, north, ["viewer"])
        _membership(db, admin, north, ["administrator"])
        # Documented demo compromise: one person can both investigate and approve.
        # Production use should keep those roles on different people.
        _membership(db, combined, north, ["operator", "approver"])
        _membership(db, harbor_operator, harbor, ["operator", "administrator"])
        crm, support = _connectors(db, north)
        _links(db, north, crm, support)
        db.commit()
        north_id = str(north.id)
    finally:
        db.close()
    added = load()
    record = {
        "passwords": passwords,
        "users": {
            "operator": "northstar.operator",
            "approver": "northstar.approver",
            "viewer": "northstar.viewer",
            "admin": "northstar.admin",
            "combined_operator_and_approver": "northstar.combined",
            "harbor_operator": "harbor.operator",
        },
        "workspaces": {"northstar-demo": north_id, "harbor-isolation": "harbor-isolation"},
        "note": "Local demo passwords. Demo reset refuses every workspace except synthetic northstar-demo.",
    }
    CREDENTIALS.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"northstar.operator {passwords['operator']}",
        f"northstar.approver {passwords['approver']}",
        f"northstar.viewer {passwords['viewer']}",
        f"northstar.admin {passwords['admin']}",
        f"northstar.combined {passwords['combined']}",
        f"harbor.operator {passwords['harbor']}",
        "northstar.combined holds operator and approver. That is a labeled demo compromise.",
        "Demo reset is refused outside the synthetic northstar-demo workspace.",
    ]
    (ROOT / "var" / "local-credentials.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"seeded northstar-demo runbooks_added={added}")
    baseline = ROOT / "evaluations" / "results" / "rules-baseline.json"
    if baseline.exists():
        db = session_factory()()
        try:
            for item in json.loads(baseline.read_text(encoding="utf-8"))["rules"]:
                existing = db.scalar(
                    select(EvaluationReport).where(EvaluationReport.approach == "rules", EvaluationReport.split == item["split"])
                )
                if existing:
                    continue
                db.add(
                    EvaluationReport(
                        approach="rules",
                        mode="deterministic",
                        provider="rules",
                        model=None,
                        split=item["split"],
                        summary={key: item[key] for key in ("sample_size", "diagnosis_correct", "next_action_correct", "unsafe_repair_proposals", "note", "seed")},
                        created_at=utcnow(),
                    )
                )
            db.commit()
        finally:
            db.close()
    if "--reset-demo" not in sys.argv:
        print("existing simulator records preserved; use the demo controls only for an intentional reset")
        return
    try:
        db = session_factory()()
        workspace = db.scalar(select(Workspace).where(Workspace.slug == "northstar-demo"))
        apply_scenario(db, workspace, "missing_update")
        print("scenario=missing_update")
    except Exception as exc:
        print(f"scenario_skipped={exc}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
