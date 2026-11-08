"""Start the bundled PostgreSQL and create isolated roles. Does not stop unrelated processes."""

from __future__ import annotations

import socket
import os
import sys
from pathlib import Path

import psycopg
from pgserver._commands import initdb, pg_ctl

ROOT = Path(__file__).resolve().parents[1]
PGDATA = ROOT / "var" / "pgdata"
LOG = ROOT / "var" / "logs" / "postgres.log"
PORT = int(os.environ.get("NORTHSTAR_PG_PORT", "55438"))

STATEMENTS = [
    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cir_owner') THEN CREATE ROLE cir_owner LOGIN PASSWORD 'cir_owner_local'; END IF; END $$;",
    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cir_app') THEN CREATE ROLE cir_app LOGIN PASSWORD 'cir_app_local'; END IF; END $$;",
    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crm_app') THEN CREATE ROLE crm_app LOGIN PASSWORD 'crm_app_local'; END IF; END $$;",
    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'support_app') THEN CREATE ROLE support_app LOGIN PASSWORD 'support_app_local'; END IF; END $$;",
]


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def ensure_server() -> None:
    PGDATA.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not (PGDATA / "PG_VERSION").exists():
        if port_in_use(PORT):
            raise SystemExit(f"Port {PORT} is already in use. This script will not stop that process.")
        initdb(["--auth=trust", "--username=postgres", "--encoding=UTF8"], pgdata=PGDATA)
    if not port_in_use(PORT):
        pg_ctl(["-w", "-o", f"-h 127.0.0.1 -p {PORT}", "-l", str(LOG), "start"], pgdata=PGDATA)
    elif not (PGDATA / "postmaster.pid").exists():
        raise SystemExit(f"Port {PORT} is in use by another process. Refusing to continue.")


def ensure_databases() -> None:
    with psycopg.connect(f"postgresql://postgres@127.0.0.1:{PORT}/postgres", autocommit=True) as conn:
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


def main() -> None:
    ensure_server()
    ensure_databases()
    print(f"postgres=postgresql://postgres@127.0.0.1:{PORT}/postgres")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"postgres setup failed: {exc}", file=sys.stderr)
        raise
