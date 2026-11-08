"""Seed only a fresh installation. Never reset an existing production volume."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
from sqlalchemy import func, select
from recovery_api.db import session_factory
from recovery_api.models import Incident, Membership, User


def main():
    root = Path(__file__).resolve().parents[1]
    marker = root / "var" / "cloud-seed-complete.json"
    if marker.exists():
        print("Persistent synthetic workspace preserved; initial seeding already completed.")
        return
    with session_factory()() as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("Existing users without a bootstrap marker: refusing any reset. Review the existing volume manually.")
    subprocess.run([sys.executable, "scripts/seed.py", "--reset-demo"], cwd=root, check=True)
    with session_factory()() as db:
        for user in db.scalars(select(User).where(User.username.in_(["northstar.combined", "harbor.operator"]))):
            user.disabled = True
            for membership in db.scalars(select(Membership).where(Membership.user_id == user.id)):
                membership.roles = []
        db.commit()
        if not db.scalar(select(func.count()).select_from(Incident)):
            raise SystemExit("Bootstrap did not produce the synthetic incident. Review internal API/webhook connectivity before proceeding.")
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps({"seeded": True, "synthetic": True}), encoding="utf-8")
    temporary.replace(marker)
    print("Initial synthetic workspace ready. Credentials remain in the private runtime volume.")


if __name__ == "__main__":
    main()
