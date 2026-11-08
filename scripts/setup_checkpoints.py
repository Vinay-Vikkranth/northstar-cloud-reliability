"""Create LangGraph checkpoint tables as the migration role and grant them to cir_app."""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api")]

from recovery_api.settings import get_settings


def main() -> None:
    settings = get_settings()
    with PostgresSaver.from_conn_string(settings.migration_psycopg_url) as saver:
        saver.setup()
    with psycopg.connect(settings.migration_psycopg_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                  checkpoint_migrations, checkpoints, checkpoint_blobs, checkpoint_writes
                TO cir_app
                """
            )
    print("checkpoints=ready")


if __name__ == "__main__":
    main()
