"""Score rules and the two offline model runners. Live calls stay opt-in."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "apps" / "api"), str(ROOT / "packages" / "recovery_core")]

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)

from evaluations.runners import (
    build_live_completer,
    live_status,
    run_live_evaluation,
    score_completer,
    score_rules,
    weak_contract_completer,
    weak_tool_completer,
)


def _cases(path: Path) -> tuple[list[dict], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["cases"], payload


def score_split(split: str) -> dict:
    cases, payload = _cases(ROOT / "datasets" / split / "cases.json")
    result = score_rules(cases, split=split, seed=payload["seed"])
    result["mode"] = "deterministic"
    result["model"] = None
    return result


def _split_path(split: str) -> Path:
    if split == "varied":
        return ROOT / "datasets" / "evaluation" / "varied.json"
    return ROOT / "datasets" / split / "cases.json"


def _run_live(args: list[str]) -> None:
    split = "varied"
    if "--split" in args:
        split = args[args.index("--split") + 1]
    limit = 5
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    cases, payload = _cases(_split_path(split))
    results = run_live_evaluation(cases, build_live_completer(), split=split, seed=payload["seed"], limit=limit)
    results["model"] = os.environ.get("CIR_LLM_MODEL")
    results["recorded_at"] = datetime.now(timezone.utc).isoformat()
    out = ROOT / "evaluations" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "live.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(
        f"live {split}: single {results['single_pass']['diagnosis_correct']}/{results['single_pass']['sample_size']} "
        f"tool {results['tool_agent']['diagnosis_correct']}/{results['tool_agent']['sample_size']}"
    )


def _run_offline() -> None:
    bundles = [
        ("development", ROOT / "datasets" / "development" / "cases.json"),
        ("evaluation", ROOT / "datasets" / "evaluation" / "cases.json"),
        ("varied", ROOT / "datasets" / "evaluation" / "varied.json"),
    ]
    rules = []
    single = []
    tools = []
    for split, path in bundles:
        cases, payload = _cases(path)
        rules.append(score_rules(cases, split=split, seed=payload["seed"]))
        single.append(
            score_completer(
                cases,
                weak_contract_completer,
                approach="single_pass",
                split=split,
                seed=payload["seed"],
                mode="single_pass",
                allow_unknown_cost=True,
            )
        )
        tools.append(
            score_completer(
                cases,
                weak_tool_completer,
                approach="tool_agent",
                split=split,
                seed=payload["seed"],
                mode="tool",
                allow_unknown_cost=True,
            )
        )
    live = live_status()
    results = {
        "rules": rules,
        "single_pass_contract": single,
        "tool_agent_contract": tools,
        "live": live,
        "comparison": "On these synthetic labels the rules match the repair policy. The contract stand-ins are weak on purpose and are not evidence that a live model is better or worse.",
    }
    out = ROOT / "evaluations" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rules-baseline.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for item in rules:
        print(
            f"rules {item['split']}: cause {item['diagnosis_correct']}/{item['sample_size']} "
            f"action {item['next_action_correct']}/{item['sample_size']} "
            f"citations {item['citation_valid']}/{item['sample_size']} unsafe {item['unsafe_repair_proposals']}"
        )
        if item["failures"]:
            print(json.dumps(item["failures"][:5], indent=2))
    for label, rows in (("single-pass contract", single), ("tool-agent contract", tools)):
        for item in rows:
            print(
                f"{label} {item['split']}: cause {item['diagnosis_correct']}/{item['sample_size']} "
                f"unsafe {item['unsafe_repair_proposals']} cost_known {item['cost_known']}"
            )
    print(f"live: {live['status']}")


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--live" in args:
        _run_live(args)
        return
    _run_offline()


if __name__ == "__main__":
    main()
