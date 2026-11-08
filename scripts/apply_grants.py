"""Give the runtime role access, then remove UPDATE/DELETE on append-only tables."""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api")]

from recovery_api.settings import get_settings

APPEND_ONLY = (
    "audit_events",
    "evidence",
    "approvals",
    "diagnoses",
    "sync_events",
    "repair_attempts",
    "verifications",
)


def main() -> None:
    settings = get_settings()
    with psycopg.connect(settings.migration_psycopg_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("GRANT USAGE ON SCHEMA public TO cir_app")
            cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO cir_app")
            cur.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO cir_app")
            cur.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cir_app")
            for table in APPEND_ONLY:
                cur.execute(f"REVOKE UPDATE, DELETE ON {table} FROM cir_app")
            cur.execute("GRANT UPDATE ON proposals TO cir_app")
            cur.execute("REVOKE DELETE ON proposals FROM cir_app")
    print("grants=cir_app")


if __name__ == "__main__":
    main()
