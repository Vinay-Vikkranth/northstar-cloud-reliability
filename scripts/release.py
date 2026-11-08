"""Content-verified releases and rollback. Cloud commands run only with --deploy."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time

INCLUDE = ("apps", "packages", "services", "migrations", "scripts", "deploy", "runbooks", "datasets", "evaluations")
ROOT_FILES = ("requirements.lock", "pyproject.toml", "alembic.ini", "Dockerfile.production", "compose.production.yaml", ".dockerignore")
SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "var", "dist", "test-results", "playwright-report"}
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$")
IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".release-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def release_lock(store):
    store = Path(store).resolve()
    store.mkdir(parents=True, exist_ok=True)
    lock = store / ".release.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("Another release operation is active; inspect the lock owner before retrying") from None
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def public_files(source):
    source = Path(source).resolve()
    for name in ROOT_FILES:
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or unsafe release file: " + name)
        yield path
    for directory in INCLUDE:
        base = source / directory
        if not base.exists():
            continue
        for current, directories, names in os.walk(base, followlinks=False):
            directories[:] = sorted(d for d in directories if d not in SKIP)
            for directory_name in directories:
                if (Path(current)/directory_name).is_symlink():
                    raise ValueError("Symlink directories are not allowed in a release snapshot")
            for name in sorted(names):
                path = Path(current)/name
                if name.startswith(".env") or name.endswith((".log", ".pyc", ".pyo", ".local.json", ".sqlite3", ".db")):
                    continue
                if path.is_symlink():
                    raise ValueError("Symlink files are not allowed in a release snapshot")
                yield path


def stage(source, store, version):
    source, store = Path(source).resolve(), Path(store).resolve()
    if not VERSION.fullmatch(version):
        raise ValueError("Use a simple release version, without paths")
    with release_lock(store):
        hashes = {str(path.relative_to(source)).replace("\\", "/"): sha(path) for path in public_files(source)}
        fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        identity = version + "-" + fingerprint[:12]
        destination = store / identity
        if destination.exists():
            verify(store, identity)
            return identity
        temporary = store / (".staging-" + secrets.token_hex(8))
        temporary.mkdir()
        for relative in hashes:
            target = temporary/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source/relative, target)
            if sha(target) != hashes[relative]:
                raise ValueError("Source changed during staging; no release was activated")
        manifest = {"id": identity, "version": version, "created": stamp(), "source_sha256": fingerprint,
                    "files": hashes, "synthetic_only": True, "schema_rollback": "application rollback only; no automatic database downgrade"}
        atomic_json(temporary/"release-manifest.json", manifest)
        os.replace(temporary, destination)
        verify(store, identity)
        return identity


def verify(store, identity):
    store = Path(store).resolve()
    if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
        raise ValueError("Invalid release identifier")
    root = store/identity
    if root.is_symlink() or root.resolve().parent != store:
        raise ValueError("Release path escapes the release store")
    manifest = json.loads((root/"release-manifest.json").read_text(encoding="utf-8"))
    if manifest["id"] != identity:
        raise ValueError("Release identity mismatch")
    expected = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected != manifest["source_sha256"] or not identity.endswith("-" + expected[:12]):
        raise ValueError("Release manifest fingerprint mismatch")
    for relative, expected_hash in manifest["files"].items():
        path = root/relative
        if Path(relative).is_absolute() or not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError("Unsafe path in release manifest")
        if not path.is_file() or sha(path) != expected_hash:
            raise ValueError("Release file checksum mismatch: " + relative)
    entries = list(root.rglob("*"))
    if any(path.is_symlink() for path in entries):
        raise ValueError("Symlinks are not allowed in immutable release snapshots")
    actual = {str(path.relative_to(root)).replace("\\", "/") for path in entries if path.is_file()}
    if actual != set(manifest["files"]) | {"release-manifest.json"}:
        raise ValueError("Unmanifested files found in immutable release snapshot")
    return manifest


def status(store):
    path = Path(store)/"state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"active": None, "previous": None, "deployed": False, "status": "empty"}


def run_stack(store, identity, env_file, runner=subprocess.run):
    root = Path(store).resolve()/identity
    private_environment = Path(env_file).resolve()
    if not private_environment.is_file() or private_environment.is_relative_to(root):
        raise ValueError("Provide a separate private environment file outside the immutable snapshot")
    environment = dict(os.environ, NORTHSTAR_IMAGE="northstar-cloud:" + identity)
    command = ["docker", "compose", "--project-name", "northstar-cloud", "--env-file", str(private_environment), "-f", str(root/"compose.production.yaml")]
    runner(command + ["up", "-d", "--build"], cwd=root, env=environment, check=True)
    probe = "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8101/health/ready',timeout=3); assert r.status==200"
    for attempt in range(20):
        try:
            runner(command + ["exec", "-T", "api", "python", "-c", probe], cwd=root, env=environment, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except subprocess.CalledProcessError:
            if attempt == 19:
                raise RuntimeError("Application readiness failed after deployment; see private container logs") from None
            time.sleep(1)


def activate(store, identity, *, deploy=False, env_file=None, runner=subprocess.run, allow_schema_change=False, expected_state=None):
    store = Path(store).resolve()
    with release_lock(store):
        manifest = verify(store, identity)
        previous = status(store)
        if expected_state is not None and previous != expected_state:
            raise ValueError("Active release changed while preparing rollback; inspect current status")
        if deploy:
            if not env_file:
                raise ValueError("--env-file is required for deployment")
            if previous["active"]:
                old = verify(store, previous["active"])
                migrations = lambda value: {key: val for key, val in value["files"].items() if key.startswith("migrations/")}
                if migrations(old) != migrations(manifest) and not allow_schema_change:
                    raise ValueError("Migration files changed; review backward compatibility and use --allow-schema-change only after review")
            try:
                run_stack(store, identity, env_file, runner)
            except Exception:
                if previous.get("deployed") and previous.get("active"):
                    try:
                        run_stack(store, previous["active"], env_file, runner)
                    except Exception:
                        atomic_json(store/"state.json", dict(previous, deployed=False, status="deployment_and_recovery_failed", updated=stamp()))
                        raise RuntimeError("Deployment and recovery failed; existing volumes were preserved, service state requires inspection") from None
                else:
                    atomic_json(store/"state.json", dict(previous, deployed=False, status="deployment_failed", updated=stamp()))
                raise RuntimeError("Deployment failed; prior deployed release was restored when available; no data volume was deleted") from None
        if previous["active"] == identity:
            prior_id = previous.get("previous")
        else:
            prior_id = previous["active"]
        state = {"active": identity, "previous": prior_id, "deployed": bool(deploy),
                 "status": "deployed_ready" if deploy else "artifact_activation_only", "updated": stamp(),
                 "source_sha256": manifest["source_sha256"]}
        atomic_json(store/"state.json", state)
        return state


def rollback(store, *, deploy=False, env_file=None, runner=subprocess.run):
    previous = status(store)
    target = previous.get("previous")
    if not target:
        raise ValueError("No prior release exists")
    return activate(store, target, deploy=deploy, env_file=env_file, runner=runner, expected_state=previous)


def init_env(output, domain, email):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", domain) or "." not in domain or ".." in domain:
        raise ValueError("Supply a DNS hostname without a scheme, path, port or wildcard")
    if not re.fullmatch(r"[^\s=@]+@[^\s=@]+\.[^\s=@]+", email):
        raise ValueError("Supply the ACME contact email")
    values = {name: secrets.token_hex(24) for name in ("POSTGRES_ADMIN_PASSWORD", "CIR_OWNER_PASSWORD", "CIR_APP_PASSWORD", "CRM_DB_PASSWORD", "SUPPORT_DB_PASSWORD", "CRM_READ_TOKEN", "CRM_ADMIN_TOKEN", "SUPPORT_TOKEN", "SUPPORT_ADMIN_TOKEN", "CIR_WEBHOOK_SECRET")}
    values.update(NORTHSTAR_DOMAIN=domain, ACME_EMAIL=email, NORTHSTAR_IMAGE="northstar-cloud:source")
    for name, role, secret_name, database in (
        ("CIR_ADMIN_URL", "postgres", "POSTGRES_ADMIN_PASSWORD", "postgres"),
        ("CIR_DATABASE_URL", "cir_app", "CIR_APP_PASSWORD", "cir"),
        ("CIR_MIGRATION_DATABASE_URL", "cir_owner", "CIR_OWNER_PASSWORD", "cir"),
        ("CRM_DATABASE_URL", "crm_app", "CRM_DB_PASSWORD", "crm"),
        ("SUPPORT_DATABASE_URL", "support_app", "SUPPORT_DB_PASSWORD", "support")):
        driver = "postgresql" if name == "CIR_ADMIN_URL" else "postgresql+psycopg"
        values[name] = f"{driver}://{role}:{values[secret_name]}@postgres:5432/{database}"
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("# PRIVATE: generated secrets; keep outside Git and release snapshots.\n")
        handle.write("\n".join(f"{name}={value}" for name, value in values.items()) + "\n")
    return {"environment_file": str(output.resolve()), "secrets_generated": 10, "cloud_activated": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["stage", "verify", "activate", "rollback", "status", "init-env"])
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--store", type=Path, default=Path("var/releases"))
    parser.add_argument("--version", default="2026.11.08.1")
    parser.add_argument("--id")
    parser.add_argument("--deploy", action="store_true", help="Actually run Docker Compose; requires approved infrastructure and --env-file")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--allow-schema-change", action="store_true")
    parser.add_argument("--domain")
    parser.add_argument("--email")
    parser.add_argument("--output", type=Path, default=Path("var/cloud/production.env"))
    args = parser.parse_args()
    if args.command == "stage":
        result = {"release_id": stage(args.source, args.store, args.version), "cloud_activated": False}
    elif args.command == "verify":
        result = verify(args.store, args.id)
    elif args.command == "activate":
        result = activate(args.store, args.id, deploy=args.deploy, env_file=args.env_file, allow_schema_change=args.allow_schema_change)
    elif args.command == "rollback":
        result = rollback(args.store, deploy=args.deploy, env_file=args.env_file)
    elif args.command == "init-env":
        result = init_env(args.output, args.domain or "", args.email or "")
    else:
        result = status(args.store)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
