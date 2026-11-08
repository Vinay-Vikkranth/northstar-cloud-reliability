"""Clean-clone packaging contracts, including the pgserver omission regression."""
import importlib.metadata as metadata
import importlib.util
from pathlib import Path
import tomllib
from packaging.requirements import Requirement

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("northstar_preflight",ROOT/"scripts/preflight.py")
preflight=importlib.util.module_from_spec(spec);spec.loader.exec_module(preflight)

def test_every_declared_runtime_dependency_has_a_compatible_exact_pin():
    result=preflight.check_lock(ROOT/"pyproject.toml",ROOT/"requirements.lock")
    assert result["missing_runtime_pins"]==[],result
    assert result["incompatible_runtime_pins"]==[],result

def test_omitted_native_postgres_package_is_detected_before_startup(tmp_path):
    broken=tmp_path/"requirements.lock"
    broken.write_text("\n".join(line for line in (ROOT/"requirements.lock").read_text().splitlines() if not line.startswith("pgserver==")),encoding="utf-8")
    result=preflight.check_lock(ROOT/"pyproject.toml",broken)
    assert result["missing_runtime_pins"]==["pgserver"]

def test_pgserver_default_dependencies_are_pinned_at_compatible_versions():
    pins={}
    for line in (ROOT/"requirements.lock").read_text().splitlines():
        if not line or line.startswith("#"):continue
        requirement=Requirement(line)
        pins[preflight.normalized(requirement.name)]=next(iter(requirement.specifier)).version
    assert metadata.version("pgserver")==pins["pgserver"]=="0.1.4"
    for declaration in metadata.requires("pgserver") or []:
        requirement=Requirement(declaration)
        if requirement.marker and not requirement.marker.evaluate({"extra":""}):continue
        assert preflight.normalized(requirement.name) in pins,requirement.name
        assert requirement.specifier.contains(pins[preflight.normalized(requirement.name)]),requirement.name
