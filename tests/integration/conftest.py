"""Integration services on ports that do not belong to a running demo.

The demo stack, when it is up, keeps 8111, 8121, and the cir/crm/support databases.
These tests use 18111, 18121, and cir_test/crm_test/support_test instead.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PG_PORT = int(os.environ.get("NORTHSTAR_TEST_PG_PORT", "55432"))
CRM_PORT = int(os.environ.get("NORTHSTAR_TEST_CRM_PORT", "18111"))
SUPPORT_PORT = int(os.environ.get("NORTHSTAR_TEST_SUPPORT_PORT", "18121"))
os.environ["CIR_DATABASE_URL"] = f"postgresql+psycopg://cir_app:cir_app_local@127.0.0.1:{PG_PORT}/cir_test"
os.environ["CIR_MIGRATION_DATABASE_URL"] = f"postgresql+psycopg://cir_owner:cir_owner_local@127.0.0.1:{PG_PORT}/cir_test"
os.environ["CIR_CRM_BASE_URL"] = f"http://127.0.0.1:{CRM_PORT}"
os.environ["CIR_SUPPORT_BASE_URL"] = f"http://127.0.0.1:{SUPPORT_PORT}"
os.environ["CRM_DATABASE_URL"] = f"postgresql+psycopg://crm_app:crm_app_local@127.0.0.1:{PG_PORT}/crm_test"
os.environ["SUPPORT_DATABASE_URL"] = f"postgresql+psycopg://support_app:support_app_local@127.0.0.1:{PG_PORT}/support_test"
os.environ["CIR_TRACE_PATH"] = str(ROOT / "var" / "traces-test.jsonl")

import httpx
import pytest

PYTHON = Path(sys.executable)
ENV = {
    **os.environ,
    "PYTHONPATH": os.pathsep.join(
        [
            str(ROOT / "apps" / "api"),
            str(ROOT / "apps" / "worker"),
            str(ROOT / "services" / "crm_simulator"),
            str(ROOT / "services" / "support_service"),
            str(ROOT / "packages" / "recovery_core"),
        ]
    ),
}


def _free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _ensure_test_databases() -> None:
    import psycopg

    statements = [
        "CREATE DATABASE cir_test OWNER cir_owner",
        "CREATE DATABASE crm_test OWNER crm_app",
        "CREATE DATABASE support_test OWNER support_app",
    ]
    admin = os.environ.get("CIR_ADMIN_URL", f"postgresql://postgres@127.0.0.1:{PG_PORT}/postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT datname FROM pg_database")
            names = {row[0] for row in cur.fetchall()}
            for statement, name in zip(statements, ("cir_test", "crm_test", "support_test"), strict=True):
                if name not in names:
                    cur.execute(statement)
            cur.execute("GRANT CONNECT ON DATABASE cir_test TO cir_owner, cir_app")
            cur.execute("GRANT CONNECT ON DATABASE crm_test TO crm_app")
            cur.execute("GRANT CONNECT ON DATABASE support_test TO support_app")
    subprocess.run([str(PYTHON), "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=ENV, check=True)
    subprocess.run([str(PYTHON), str(ROOT / "scripts" / "apply_grants.py")], cwd=ROOT, env=ENV, check=True)
    subprocess.run([str(PYTHON), str(ROOT / "scripts" / "setup_checkpoints.py")], cwd=ROOT, env=ENV, check=True)
    from recovery_api.db import reset_engine
    from recovery_api.settings import get_settings

    get_settings.cache_clear()
    reset_engine()


@pytest.fixture(scope="session")
def services():
    for port in (CRM_PORT, SUPPORT_PORT):
        if not _free(port):
            pytest.fail(f"Port {port} is in use. Tests will not stop an unrelated process.")
    _ensure_test_databases()
    commands = [
        [str(PYTHON), "-m", "uvicorn", "crm_simulator.app:app", "--host", "127.0.0.1", "--port", str(CRM_PORT)],
        [str(PYTHON), "-m", "uvicorn", "support_service.app:app", "--host", "127.0.0.1", "--port", str(SUPPORT_PORT)],
    ]
    log_dir = ROOT / "var"
    log_dir.mkdir(exist_ok=True)
    logs = [(log_dir / "crm-test.log").open("w", encoding="utf-8"), (log_dir / "support-test.log").open("w", encoding="utf-8")]
    processes = [
        subprocess.Popen(cmd, cwd=ROOT, env=ENV, stdout=log, stderr=subprocess.STDOUT)
        for cmd, log in zip(commands, logs, strict=True)
    ]
    try:
        deadline = time.time() + 40
        ready = False
        while time.time() < deadline:
            try:
                a = httpx.get(f"http://127.0.0.1:{CRM_PORT}/health/ready", timeout=1)
                b = httpx.get(f"http://127.0.0.1:{SUPPORT_PORT}/health/ready", timeout=1)
                if a.status_code == 200 and b.status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        if not ready:
            for proc in processes:
                proc.kill()
            for log in logs:
                log.close()
            crm_log = (log_dir / "crm-test.log").read_text(encoding="utf-8")[-2000:]
            support_log = (log_dir / "support-test.log").read_text(encoding="utf-8")[-2000:]
            pytest.fail("services did not become ready\n" + crm_log + "\n" + support_log)
        yield
    finally:
        for proc in processes:
            proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        for log in logs:
            log.close()
