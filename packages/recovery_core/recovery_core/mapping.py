from recovery_core import MAPPING_VERSION, PLAN_VALUES

# Local versioned translations. External values are not accepted until a version lists them.
MAPPINGS: dict[str, dict[str, str]] = {
    "v1": {
        "basic": "basic",
        "premium": "premium",
        "enterprise": "enterprise",
        "starter": "basic",
        "pro": "premium",
        "business": "enterprise",
    }
}


def normalize_plan(raw: object, version: str = MAPPING_VERSION) -> tuple[str | None, str | None]:
    """Return (canonical plan, error code). Error is None when the value maps."""
    table = MAPPINGS.get(version)
    if table is None:
        return None, "unknown_mapping_version"
    if raw is None:
        return None, "missing_field"
    if not isinstance(raw, str):
        return None, "unmapped"
    key = raw.strip()
    if not key:
        return None, "missing_field"
    if key in table:
        canonical = table[key]
    elif key.lower() in table:
        canonical = table[key.lower()]
    else:
        return None, "unmapped"
    if canonical not in PLAN_VALUES:
        return None, "unmapped"
    return canonical, None
