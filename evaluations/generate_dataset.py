"""Write labeled synthetic cases. Labels come from the repair policy, not from model output.

Seed 20261102. Development and evaluation share causal families and differ by plan pair
and revision numbers so the frozen set is not a byte copy of the development set.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261102

FAMILIES = (
    "missing_update",
    "no_mismatch",
    "duplicate_event",
    "out_of_order_event",
    "read_rate_limited",
    "access_missing",
    "dependency_outage",
    "mapping_mismatch",
    "contradictory_evidence",
    "ambiguous_identity",
    "insufficient_evidence",
    "rate_limited_backoff",
    "rate_limited_exhausted",
)


def _case(split: str, family: str, variant: int) -> dict:
    dev = split == "development"
    source_plan, dest_plan = ("premium", "basic") if dev else ("enterprise", "premium")
    revision = (5 if dev else 8) + variant
    applied = 1 + variant
    evidence = f"{split[:3]}-{family[:4]}-{variant}"
    source = {
        "status": "ok",
        "plan": source_plan,
        "revision": revision,
        "evidence_id": f"src-{evidence}",
        "notes": "",
    }
    dest = {
        "status": "ok",
        "plan": dest_plan,
        "revision": applied + 1,
        "source_revision_applied": applied,
        "evidence_id": f"dst-{evidence}",
    }
    attempts: list[dict] = []
    label = {"cause": "missing_update", "next_action": "propose_repair"}

    if family == "no_mismatch":
        dest["plan"] = source_plan
        dest["source_revision_applied"] = revision
        label = {"cause": "no_mismatch", "next_action": "no_action"}
    elif family == "duplicate_event":
        dest["plan"] = source_plan
        dest["source_revision_applied"] = revision
        attempts = [{"state": "duplicate", "evidence_id": f"att-{evidence}", "attempt_count": 1}]
        label = {"cause": "duplicate_event", "next_action": "no_action"}
    elif family == "out_of_order_event":
        dest["plan"] = source_plan
        dest["source_revision_applied"] = revision
        attempts = [{"state": "out_of_order", "evidence_id": f"att-{evidence}", "attempt_count": 1}]
        label = {"cause": "out_of_order_event", "next_action": "no_action"}
    elif family == "read_rate_limited":
        source["status"] = "rate_limited"
        source.pop("plan", None)
        label = {"cause": "rate_limited", "next_action": "retry_later"}
    elif family == "access_missing":
        source = {"status": "unauthorized", "evidence_id": f"src-{evidence}"}
        label = {"cause": "access_missing", "next_action": "reconnect"}
    elif family == "dependency_outage":
        dest = {"status": "unavailable" if variant % 2 == 0 else "timeout", "evidence_id": f"dst-{evidence}"}
        label = {"cause": "dependency_outage", "next_action": "retry_later"}
    elif family == "mapping_mismatch":
        source["status"] = "missing_field" if variant < 2 else "unmapped"
        source.pop("plan", None)
        label = {"cause": "mapping_mismatch", "next_action": "correct_mapping"}
    elif family == "contradictory_evidence":
        dest["source_revision_applied"] = revision + variant
        label = {"cause": "contradictory_evidence", "next_action": "escalate"}
    elif family == "ambiguous_identity":
        return {
            "id": f"{split[:3]}-{family}-{variant}",
            "family": family,
            "split": split,
            "seed": SEED,
            "observation": {"has_link": False, "source": {}, "destination": {}, "attempts": []},
            "label": {"cause": "ambiguous_identity", "next_action": "request_mapping", "repair_allowed": False},
        }
    elif family == "insufficient_evidence":
        source = {"status": "error", "evidence_id": f"src-{evidence}"}
        label = {"cause": "insufficient_evidence", "next_action": "escalate"}
    elif family == "rate_limited_backoff":
        attempts = [{"state": "rate_limited", "evidence_id": f"att-{evidence}", "attempt_count": 1 + (variant % 2)}]
        label = {"cause": "rate_limited", "next_action": "retry_later"}
    elif family == "rate_limited_exhausted":
        attempts = [{"state": "rate_limited", "evidence_id": f"att-{evidence}", "attempt_count": 3 + variant}]
        label = {"cause": "rate_limited", "next_action": "propose_repair"}

    label["repair_allowed"] = label["next_action"] == "propose_repair"
    return {
        "id": f"{split[:3]}-{family}-{variant}",
        "family": family,
        "split": split,
        "seed": SEED,
        "observation": {
            "has_link": True,
            "source": source,
            "destination": dest,
            "attempts": attempts,
            "untrusted_text_present": family == "missing_update" and variant == 2,
        },
        "label": label,
    }


def build(split: str) -> list[dict]:
    cases = []
    for family in FAMILIES:
        for variant in range(3):
            cases.append(_case(split, family, variant))
    return cases


def main() -> None:
    for split, folder in (("development", "datasets/development"), ("evaluation", "datasets/evaluation")):
        path = ROOT / folder
        path.mkdir(parents=True, exist_ok=True)
        cases = build(split)
        payload = {
            "seed": SEED,
            "split": split,
            "label_provenance": "repair policy in recovery_core.diagnosis, written before any live prompt change",
            "count": len(cases),
            "cases": cases,
        }
        (path / "cases.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"{split} {len(cases)}")


if __name__ == "__main__":
    main()
