"""One-time local admin bootstrap. The password is printed once and is not a shared default."""

import argparse
import secrets

from sqlalchemy import func, select

from recovery_api.db import session_factory
from recovery_api.models import Membership, User, Workspace
from recovery_api.security import hash_password, utcnow


def bootstrap(username: str, workspace_name: str) -> None:
    db = session_factory()()
    try:
        existing = db.scalar(select(func.count()).select_from(User))
        if existing:
            raise SystemExit("Bootstrap already completed. Refusing to create another initial password.")
        password = secrets.token_urlsafe(18)
        user = User(username=username, password_hash=hash_password(password), disabled=False, created_at=utcnow())
        workspace = Workspace(name=workspace_name, slug="northstar-demo", synthetic=True, created_at=utcnow())
        db.add(user)
        db.add(workspace)
        db.flush()
        db.add(
            Membership(
                user_id=user.id,
                workspace_id=workspace.id,
                roles=["administrator"],
                created_at=utcnow(),
            )
        )
        db.commit()
        print(f"username={username}")
        print(f"password={password}")
        print("workspace=northstar-demo")
        print("Store this password now. It is not written to the application log.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first local administrator.")
    parser.add_argument("--username", default="northstar.admin")
    parser.add_argument("--workspace-name", default="Northstar Demo")
    args = parser.parse_args()
    bootstrap(args.username, args.workspace_name)


if __name__ == "__main__":
    main()
