"""Shared domain rules for customer integration recovery."""

WORKFLOW_VERSION = "inv-2026.11.02"
MAPPING_VERSION = "v1"
PLAN_VALUES = frozenset({"basic", "premium", "enterprise"})
WRITABLE_FIELDS = frozenset({"plan"})
