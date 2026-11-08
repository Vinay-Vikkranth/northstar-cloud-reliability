"""Dump the domain database and restore it into a throwaway database, then drop that database."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / ".venv" / "Lib" / "site-packages" / "pgserver" / "pginstall" / "bin"
DUMP = ROOT / "var" / "cir-backup.sql"


def _tool(name: str) -> Path:
    path = BIN / (name + (".exe" if os.name == "nt" else ""))
    if not path.exists():
        raise SystemExit(f"Missing {path}. The bundled PostgreSQL tools are required for this local check.")
    return path


def main() -> None:
    DUMP.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PGPASSWORD": "cir_owner_local"}
    subprocess.run(
        [str(_tool("pg_dump")), "-h", "127.0.0.1", "-p", "55432", "-U", "cir_owner", "-d", "cir", "-f", str(DUMP), "--no-owner"],
        check=True,
        env=env,
    )
    admin = {**os.environ, "PGPASSWORD": ""}
    subprocess.run(
        [str(_tool("psql")), "-h", "127.0.0.1", "-p", "55432", "-U", "postgres", "-d", "postgres", "-c", "DROP DATABASE IF EXISTS cir_restore_check"],
        check=True,
        env=admin,
    )
    subprocess.run(
        [str(_tool("psql")), "-h", "127.0.0.1", "-p", "55432", "-U", "postgres", "-d", "postgres", "-c", "CREATE DATABASE cir_restore_check OWNER cir_owner"],
        check=True,
        env=admin,
    )
    subprocess.run(
        [str(_tool("psql")), "-h", "127.0.0.1", "-p", "55432", "-U", "cir_owner", "-d", "cir_restore_check", "-f", str(DUMP)],
        check=True,
        env=env,
    )
    count = subprocess.run(
        [str(_tool("psql")), "-h", "127.0.0.1", "-p", "55432", "-U", "cir_owner", "-d", "cir_restore_check", "-tAc", "SELECT count(*) FROM workspaces"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    subprocess.run(
        [str(_tool("psql")), "-h", "127.0.0.1", "-p", "55432", "-U", "postgres", "-d", "postgres", "-c", "DROP DATABASE cir_restore_check"],
        check=True,
        env=admin,
    )
    print(f"restore_workspaces={count.stdout.strip()} dump={DUMP}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"backup/restore failed: {exc}", file=sys.stderr)
        raise
