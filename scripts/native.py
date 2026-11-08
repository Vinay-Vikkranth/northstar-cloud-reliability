"""Portable, isolated native demo. Foreground controller owns only its children."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import psutil

ROOT = Path(__file__).resolve().parents[1]
PARTS = ["apps/api","apps/worker","services/crm_simulator","services/support_service","packages/recovery_core","evaluations"]

def environment() -> dict:
    env = dict(os.environ)
    pg = int(env.setdefault("NORTHSTAR_PG_PORT","55438"))
    crm = int(env.setdefault("NORTHSTAR_CRM_PORT","28611"))
    support = int(env.setdefault("NORTHSTAR_SUPPORT_PORT","28621"))
    api = int(env.setdefault("NORTHSTAR_API_PORT","28601"))
    env.update({"PYTHONPATH":os.pathsep.join(str(ROOT/p) for p in PARTS),
        "CIR_DATABASE_URL":f"postgresql+psycopg://cir_app:cir_app_local@127.0.0.1:{pg}/cir",
        "CIR_MIGRATION_DATABASE_URL":f"postgresql+psycopg://cir_owner:cir_owner_local@127.0.0.1:{pg}/cir",
        "CRM_DATABASE_URL":f"postgresql+psycopg://crm_app:crm_app_local@127.0.0.1:{pg}/crm",
        "SUPPORT_DATABASE_URL":f"postgresql+psycopg://support_app:support_app_local@127.0.0.1:{pg}/support",
        "CIR_CRM_BASE_URL":f"http://127.0.0.1:{crm}","CIR_SUPPORT_BASE_URL":f"http://127.0.0.1:{support}",
        "CIR_CORS_ORIGINS":f"http://127.0.0.1:{api},http://localhost:{api}",
        "CIR_INVESTIGATOR_MODE":"deterministic","CIR_ENVIRONMENT":"local","HUBSPOT_TOKEN":"",
        "HUBSPOT_CONTACT_ID":"","CIR_LLM_API_KEY":"","CIR_LLM_MODEL":"",
        "CIR_LOG_DIR":str(ROOT/"var/logs/native"),"CIR_TRACE_PATH":str(ROOT/"var/traces-native.jsonl")})
    return env

def run(arguments: list[str], env: dict):
    subprocess.run([sys.executable, "-B", *arguments], cwd=ROOT, env=env, check=True)

def free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1",port)) != 0

def ready(url: str, process: subprocess.Popen, seconds: int=45):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Child exited with {process.returncode}; inspect var/logs/native")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:return
        except Exception:
            time.sleep(.15)
    raise RuntimeError(f"Health timeout: {url}")

def prepare(env: dict):
    run(["scripts/preflight.py"],env)
    run(["scripts/local_postgres.py"],env)
    run(["-m","alembic","upgrade","head"],env)
    run(["scripts/apply_grants.py"],env)
    run(["scripts/setup_checkpoints.py"],env)

def test(env: dict):
    prepare(env)
    env.update({"NORTHSTAR_TEST_PG_PORT":env["NORTHSTAR_PG_PORT"],
        "NORTHSTAR_TEST_CRM_PORT":"28711","NORTHSTAR_TEST_SUPPORT_PORT":"28721",
        "CIR_ADMIN_URL":f"postgresql://postgres@127.0.0.1:{env['NORTHSTAR_PG_PORT']}/postgres"})
    run(["-m","pytest","tests/unit","tests/integration","-q"],env)

def serve(env: dict):
    ports = {name:int(env[name]) for name in ("NORTHSTAR_CRM_PORT","NORTHSTAR_SUPPORT_PORT","NORTHSTAR_API_PORT")}
    busy = [port for port in ports.values() if not free(port)]
    if busy:raise SystemExit(f"Port(s) already in use: {busy}. Refusing to stop any process.")
    if not (ROOT/"apps/web/dist/index.html").exists():
        raise SystemExit("Build the UI first: cd apps/web && npm ci && npm run build")
    prepare(env)
    logs = ROOT/"var/logs/native";logs.mkdir(parents=True,exist_ok=True)
    processes=[];handles=[]
    stop_marker=ROOT/"var/stop-native.request"
    stop_marker.unlink(missing_ok=True)
    def launch(name: str, args: list[str]):
        handle=(logs/f"{name}.log").open("a",encoding="utf-8");handles.append(handle)
        child_env={**env,"OTEL_SERVICE_NAME":f"northstar-{name}","CIR_TRACE_PATH":str(ROOT/f"var/traces-{name}.jsonl"),"CIR_LOG_DIR":str(logs/name)}
        child=subprocess.Popen([sys.executable,"-B",*args],cwd=ROOT,env=child_env,stdout=handle,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        processes.append(child);return child
    try:
        for name,module,port_key in (("crm","crm_simulator.app:app","NORTHSTAR_CRM_PORT"),("support","support_service.app:app","NORTHSTAR_SUPPORT_PORT")):
            port=ports[port_key]
            proc=launch(name,["-m","uvicorn",module,"--host","127.0.0.1","--port",str(port)])
            ready(f"http://127.0.0.1:{port}/health/ready",proc)
        # This is an isolated database, so first-time synthetic initialization is safe.
        first = not (ROOT/"var/local-credentials.json").exists()
        run(["scripts/seed.py",*(["--reset-demo"] if first else [])],env)
        api=launch("api",["-m","uvicorn","recovery_api.main:app","--host","127.0.0.1","--port",str(ports['NORTHSTAR_API_PORT'])])
        ready(f"http://127.0.0.1:{ports['NORTHSTAR_API_PORT']}/health/ready",api)
        launch("worker",["-m","recovery_worker.main"])
        print(f"Northstar ready: http://127.0.0.1:{ports['NORTHSTAR_API_PORT']}",flush=True)
        print("Synthetic credentials are in var/local-credentials.txt. Ctrl+C stops only these child services.",flush=True)
        while all(proc.poll() is None for proc in processes):
            if stop_marker.exists():return
            time.sleep(.5)
        raise RuntimeError("A child stopped. Inspect var/logs/native before restarting.")
    finally:
        for child in reversed(processes):
            if child.poll() is None:
                # On Windows a venv python.exe launcher may own the actual
                # interpreter as a child. Stop only this Popen handle's tree.
                try:
                    descendants=psutil.Process(child.pid).children(recursive=True)
                    for descendant in reversed(descendants):
                        try:descendant.terminate()
                        except psutil.NoSuchProcess:pass
                except psutil.NoSuchProcess:descendants=[]
                child.terminate()
                _,alive=psutil.wait_procs(descendants,timeout=5)
                for descendant in alive:
                    try:descendant.kill()
                    except psutil.NoSuchProcess:pass
        for child in processes:
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill()
        for handle in handles:handle.close()
        stop_marker.unlink(missing_ok=True)

def stop(env: dict):
    marker=ROOT/"var/stop-native.request"
    marker.parent.mkdir(parents=True,exist_ok=True)
    marker.write_text("Stop only this repository's native controller\n",encoding="utf-8")
    print("Stop requested. The controller closes only its owned service processes. PostgreSQL remains available.")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["prepare","test","serve","stop"])
    args=parser.parse_args();env=environment()
    {"prepare":prepare,"test":test,"serve":serve,"stop":stop}[args.command](env)

if __name__ == "__main__":
    try:main()
    except KeyboardInterrupt:pass
