"""Create the application roles on an already running PostgreSQL. Used by CI and Compose."""

from __future__ import annotations

import os

import psycopg

URL = os.environ.get("CIR_ADMIN_URL", "postgresql://postgres@127.0.0.1:55438/postgres")
STATEMENTS = [
    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cir_owner') THEN CREATE ROLE cir_owner LOGIN PASSWORD 'cir_owner_local'; END IF; END $$;",
    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cir_app') THEN CREATE ROLE cir_app LOGIN PASSWORD 'cir_app_local'; END IF; END $$;",
    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crm_app') THEN CREATE ROLE crm_app LOGIN PASSWORD 'crm_app_local'; END IF; END $$;",
    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'support_app') THEN CREATE ROLE support_app LOGIN PASSWORD 'support_app_local'; END IF; END $$;",
]


def main() -> None:
    with psycopg.connect(URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            for statement in STATEMENTS:
                cur.execute(statement)
            cur.execute("SELECT datname FROM pg_database")
            names = {row[0] for row in cur.fetchall()}
            for database, owner in (("cir", "cir_owner"), ("crm", "crm_app"), ("support", "support_app")):
                if database not in names:
                    cur.execute(f"CREATE DATABASE {database} OWNER {owner}")
            cur.execute("REVOKE CONNECT ON DATABASE crm FROM PUBLIC")
            cur.execute("REVOKE CONNECT ON DATABASE support FROM PUBLIC")
            cur.execute("GRANT CONNECT ON DATABASE crm TO crm_app")
            cur.execute("GRANT CONNECT ON DATABASE support TO support_app")
            cur.execute("GRANT CONNECT ON DATABASE cir TO cir_owner, cir_app")
    print("roles=ready")


if __name__ == "__main__":
    main()
