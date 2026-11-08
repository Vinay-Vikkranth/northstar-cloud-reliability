"""Dependency preflight. Never connects to a database, CRM or model endpoint."""
from __future__ import annotations
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]

def normalized(name):return name.lower().replace("_","-").replace(".","-")

def check_lock(project_path: Path, lock_path: Path) -> dict:
    from packaging.requirements import Requirement
    requirements=tomllib.loads(project_path.read_text(encoding="utf-8"))["project"]["dependencies"]
    pins={}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):continue
        requirement=Requirement(line)
        exact=[item.version for item in requirement.specifier if item.operator=="=="]
        if len(exact)!=1:raise ValueError(f"Lock entry is not exactly pinned: {requirement.name}")
        pins[normalized(requirement.name)]=exact[0]
    missing=[];incompatible=[]
    for declaration in requirements:
        requirement=Requirement(declaration)
        if requirement.marker and not requirement.marker.evaluate():continue
        pin=pins.get(normalized(requirement.name))
        if pin is None:missing.append(requirement.name)
        elif not requirement.specifier.contains(pin):incompatible.append(requirement.name)
    return {"missing_runtime_pins":missing,"incompatible_runtime_pins":incompatible,"pin_count":len(pins)}

def main():
    try:lock=check_lock(ROOT/"pyproject.toml",ROOT/"requirements.lock")
    except (ImportError,ValueError) as exc:
        print(json.dumps({"status":"dependencies_missing","hint":"Install with python -m pip install -r requirements.lock","detail":str(exc)}))
        return 1
    modules={"pgserver":"pgserver","psycopg":"psycopg","sqlalchemy":"sqlalchemy","fastapi":"fastapi","uvicorn":"uvicorn","langgraph":"langgraph","psutil":"psutil","opentelemetry-sdk":"opentelemetry.sdk"}
    absent=[];installed={}
    for distribution,module in modules.items():
        try:
            if importlib.util.find_spec(module) is None:raise ImportError(module)
            installed[distribution]=metadata.version(distribution)
        except (ImportError,metadata.PackageNotFoundError):absent.append(distribution)
    success=not absent and not lock["missing_runtime_pins"] and not lock["incompatible_runtime_pins"]
    print(json.dumps({"status":"ready" if success else "dependencies_missing","lock":lock,"installed":installed,"missing_installed_packages":absent,"network_calls":0}))
    if not success:print("Install the complete locked environment before starting: python -m pip install -r requirements.lock",file=sys.stderr)
    return 0 if success else 1

if __name__=="__main__":raise SystemExit(main())
