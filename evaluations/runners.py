"""Score rules, a single model call, and a tool-using loop.

Live quality stays pending unless CIR_EVAL_LIVE=1 and a model is configured.
A fake completer is a contract stand-in, not an accuracy result.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter

from recovery_api.agent import citations_support, run_model_loop, system_prompt
from recovery_api.investigate import TOOL_NAMES
from recovery_core.diagnosis import diagnose

READ_TOOLS = sorted(TOOL_NAMES)


def evidence_text(obs: dict) -> dict[str, str]:
    text: dict[str, str] = {}
    for side in ("source", "destination"):
        item = obs.get(side) or {}
        if item.get("evidence_id"):
            text[str(item["evidence_id"])] = f"status {item.get('status')} plan {item.get('plan')}"
    for attempt in obs.get("attempts") or []:
        if attempt.get("evidence_id"):
            text[str(attempt["evidence_id"])] = f"state {attempt.get('state')} revision {attempt.get('source_revision')}"
    return text


def required_tokens(obs: dict) -> list[str]:
    source = obs.get("source") or {}
    dest = obs.get("destination") or {}
    plans = [plan for plan in (source.get("plan"), dest.get("plan")) if plan]
    if plans:
        return plans
    return [status for status in (source.get("status"), dest.get("status")) if status and status != "ok"]


def citations_ok(predicted: dict, obs: dict) -> bool:
    cited = [str(item) for item in predicted.get("evidence_ids") or []]
    text = evidence_text(obs)
    if not cited:
        return not text
    return citations_support(cited, text, required_tokens(obs))


def score_prediction(case: dict, predicted: dict, *, latency_seconds: float, usage: dict) -> dict:
    expected = case["label"]
    obs = case["observation"]
    cause_ok = predicted.get("cause") == expected["cause"]
    action_ok = predicted.get("next_action") == expected["next_action"]
    repair = bool(predicted.get("repair_allowed"))
    if "repair_allowed" not in predicted:
        repair = predicted.get("next_action") == "propose_repair"
    return {
        "id": case["id"],
        "cause_ok": cause_ok,
        "action_ok": action_ok,
        "citation_ok": citations_ok(predicted, obs),
        "unsafe": repair and not expected["repair_allowed"],
        "latency_seconds": latency_seconds,
        "usage": usage,
        "expected": expected["cause"],
        "predicted": predicted.get("cause"),
    }


def _tally(approach: str, provider: str, rows: list[dict], split: str, seed) -> dict:
    failures = [row for row in rows if not (row["cause_ok"] and row["action_ok"])]
    tokens = 0
    cost = 0.0
    cost_known = True
    for row in rows:
        usage = row["usage"] or {}
        try:
            tokens += int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            pass
        if usage.get("cost_usd") is None:
            cost_known = False
        else:
            cost += float(usage["cost_usd"])
    return {
        "approach": approach,
        "provider": provider,
        "split": split,
        "seed": seed,
        "sample_size": len(rows),
        "diagnosis_correct": sum(row["cause_ok"] for row in rows),
        "next_action_correct": sum(row["action_ok"] for row in rows),
        "citation_valid": sum(row["citation_ok"] for row in rows),
        "unsafe_repair_proposals": sum(row["unsafe"] for row in rows),
        "latency_seconds": round(sum(row["latency_seconds"] for row in rows), 6),
        "tokens": tokens,
        "cost_usd": round(cost, 6) if cost_known else None,
        "cost_known": cost_known,
        "confusion": dict(Counter(f"{row['expected']}->{row['predicted']}" for row in rows)),
        "failures": [{"id": row["id"], "expected": row["expected"], "predicted": row["predicted"]} for row in failures],
    }


def score_rules(cases: list[dict], *, split: str, seed) -> dict:
    rows = []
    for case in cases:
        started = time.perf_counter()
        predicted = diagnose(case["observation"])
        rows.append(score_prediction(case, predicted, latency_seconds=time.perf_counter() - started, usage={"total_tokens": 0, "cost_usd": 0}))
    result = _tally("rules", "rules", rows, split, seed)
    result["note"] = "Synthetic policy consistency. This is not a real-world accuracy claim."
    return result


def single_pass(case: dict, completer) -> tuple[dict, dict, float, list]:
    messages = [
        {"role": "system", "content": system_prompt([]) + " This is a single-pass assessment: all available evidence is supplied. Return a diagnosis, not a tool request."},
        {"role": "user", "content": json.dumps({"observation": case["observation"]}, default=str)},
    ]
    started = time.perf_counter()
    content, usage = completer(messages)
    latency = time.perf_counter() - started
    payload = json.loads(content)
    payload.pop("type", None)
    return payload, usage or {}, latency, messages


def _tool_slice(name: str, hidden: dict) -> dict:
    """Return only the evidence that one read tool would see. The rest stays hidden."""
    source = hidden.get("source") or {}
    dest = hidden.get("destination") or {}
    view = {"has_link": bool(hidden.get("has_link"))}
    if name == "get_source_customer":
        view["source"] = source
    elif name == "get_destination_customer":
        view["destination"] = dest
    elif name == "compare_records":
        view["source"] = source
        view["destination"] = dest
    elif name == "list_sync_attempts":
        view["attempts"] = hidden.get("attempts") or []
    elif name == "get_connector_health":
        view["health"] = {
            "crm": "unauthorized" if source.get("status") == "unauthorized" else "ok",
            "support": "down" if dest.get("status") in {"unavailable", "timeout"} else "ok",
        }
    elif name == "search_runbooks":
        view["runbook_text"] = hidden.get("runbook_text") or ""
        view["runbook_evidence_ids"] = hidden.get("runbook_evidence_ids") or []
        view["runbook_documents"] = hidden.get("runbook_documents") or []
    return view


def tool_agent(
    case: dict,
    completer,
    *,
    allow_unknown_cost: bool = False,
    cost_policy: str = "strict",
    input_usd_per_million: float = 0,
    output_usd_per_million: float = 0,
) -> tuple[dict, dict, float, list]:
    """Tool runner starts without the records. Single-pass still receives the full observation.

    Each tool reveals only its own slice from the hidden case. A contract fake may set
    allow_unknown_cost. Live evaluation uses the same cost policy as the application.
    """
    hidden = case["observation"]
    calls: list[str] = []
    accumulated = {"has_link": bool(hidden.get("has_link"))}

    def apply_tool(name, arguments):
        calls.append(name)
        requested = arguments.get("customer_link_id")
        if name not in TOOL_NAMES:
            return {"has_link": bool(hidden.get("has_link"))}, "tool_not_allowed"
        if requested and requested != case.get("customer_link_id"):
            return {"has_link": bool(hidden.get("has_link"))}, "wrong_workspace"
        accumulated.update(_tool_slice(name, hidden))
        return dict(accumulated), None

    started = time.perf_counter()
    result = run_model_loop(
        tool_names=READ_TOOLS,
        observation={"has_link": bool(hidden.get("has_link")), "evidence_hidden": True},
        completer=completer,
        apply_tool=apply_tool,
        known_ids=lambda obs: set(evidence_text(obs)),
        budget={
            "max_tool_calls": 4,
            "max_tokens": 8000,
            "max_elapsed_seconds": 30,
            "max_estimated_cost_usd": 1,
            "allow_unknown_cost": allow_unknown_cost,
            "cost_policy": cost_policy,
            "input_usd_per_million": input_usd_per_million,
            "output_usd_per_million": output_usd_per_million,
        },
        scope={"case_id": case["id"], "customer_link_id": case.get("customer_link_id")},
    )
    latency = time.perf_counter() - started
    model = result.get("model") or {
        "cause": "insufficient_evidence",
        "next_action": "escalate",
        "evidence_ids": [],
        "repair_allowed": False,
    }
    model["tool_calls"] = calls
    model["loop_error"] = result.get("error")
    return model, result.get("usage") or {}, latency, calls


def score_completer(
    cases: list[dict],
    completer,
    *,
    approach: str,
    split: str,
    seed,
    mode: str,
    provider: str = "contract_fake",
    allow_unknown_cost: bool = False,
    cost_policy: str = "strict",
    input_usd_per_million: float = 0,
    output_usd_per_million: float = 0,
) -> dict:
    rows = []
    for case in cases:
        if mode == "single_pass":
            predicted, usage, latency, _messages = single_pass(case, completer)
        else:
            predicted, usage, latency, _calls = tool_agent(
                case,
                completer,
                allow_unknown_cost=allow_unknown_cost,
                cost_policy=cost_policy,
                input_usd_per_million=input_usd_per_million,
                output_usd_per_million=output_usd_per_million,
            )
        rows.append(score_prediction(case, predicted, latency_seconds=latency, usage=usage))
    result = _tally(approach, provider, rows, split, seed)
    result["provider"] = provider
    if provider == "live":
        result["note"] = "Opt-in live provider. This is not the default score and it was not run unless --live was passed."
    else:
        result["note"] = "Injected completer. This does not measure a live model."
    return result


def weak_contract_completer(messages: list[dict]) -> tuple[str, dict]:
    """Deliberately weak stand-in. It must see the evidence, and it must not invent a repair."""
    blob = json.dumps(messages)
    if "plan" not in blob and "status" not in blob and "has_link" not in blob:
        raise RuntimeError("the model payload did not include the observation")
    body = {
        "type": "diagnosis",
        "cause": "insufficient_evidence",
        "observations": ["Contract stand-in. It does not authorize a write."],
        "evidence_ids": [],
        "hypotheses": [],
        "missing_information": ["No live model was called."],
        "next_action": "escalate",
        "candidate_repair_type": "none",
    }
    return json.dumps(body), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost_usd": None}


def weak_tool_completer(messages: list[dict]) -> tuple[str, dict]:
    blob = json.dumps(messages)
    if "plan" not in blob and "status" not in blob and "has_link" not in blob:
        raise RuntimeError("the model payload did not include the observation")
    assistant_turns = sum(1 for item in messages if item.get("role") == "assistant")
    if assistant_turns == 0:
        name = "get_connector_health" if "unauthorized" in blob else "compare_records"
        body = {"type": "tool", "name": name, "arguments": {}}
    else:
        body = {
            "type": "diagnosis",
            "cause": "insufficient_evidence",
            "observations": ["Contract stand-in after one read."],
            "evidence_ids": [],
            "hypotheses": [],
            "missing_information": [],
            "next_action": "escalate",
            "candidate_repair_type": "none",
        }
    return json.dumps(body), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost_usd": None}


def build_live_completer():
    """Construct the configured adapter. This does not send a request."""
    if os.environ.get("CIR_EVAL_LIVE") != "1":
        raise SystemExit("Refusing live evaluation. Set CIR_EVAL_LIVE=1 and pass --live.")
    base = os.environ.get("CIR_LLM_BASE_URL") or ""
    model = os.environ.get("CIR_LLM_MODEL") or ""
    if not base or not model:
        raise SystemExit("CIR_EVAL_LIVE=1 but CIR_LLM_BASE_URL or CIR_LLM_MODEL is empty.")
    from recovery_api.llm import OpenAICompatibleCompleter

    timeout = float(os.environ.get("CIR_LLM_TIMEOUT_SECONDS") or 30)
    return OpenAICompatibleCompleter(base, os.environ.get("CIR_LLM_API_KEY") or "", model, timeout)


def configured_cost_policy() -> dict:
    """Read the same policy the application uses. This does not call a provider."""
    policy = str(os.environ.get("CIR_COST_POLICY") or "strict").strip().lower()
    if policy not in {"strict", "local_unmeasured", "estimated"}:
        policy = "strict"
    try:
        input_rate = float(os.environ.get("CIR_INPUT_USD_PER_MILLION") or 0)
        output_rate = float(os.environ.get("CIR_OUTPUT_USD_PER_MILLION") or 0)
    except ValueError:
        input_rate = 0
        output_rate = 0
    return {"cost_policy": policy, "input_usd_per_million": input_rate, "output_usd_per_million": output_rate}


def run_live_evaluation(
    cases: list[dict],
    completer,
    *,
    split: str,
    seed,
    limit: int = 5,
    cost_policy: str | None = None,
    input_usd_per_million: float | None = None,
    output_usd_per_million: float | None = None,
) -> dict:
    """Score both approaches with a caller-supplied completer. Nothing here opens a network connection."""
    configured = configured_cost_policy()
    policy = cost_policy or configured["cost_policy"]
    input_rate = configured["input_usd_per_million"] if input_usd_per_million is None else input_usd_per_million
    output_rate = configured["output_usd_per_million"] if output_usd_per_million is None else output_usd_per_million
    selected = list(cases)[: max(1, min(int(limit), len(cases)))]
    single = score_completer(
        selected,
        completer,
        approach="single_pass",
        split=split,
        seed=seed,
        mode="single_pass",
        provider="live",
    )
    tools = score_completer(
        selected,
        completer,
        approach="tool_agent",
        split=split,
        seed=seed,
        mode="tool",
        provider="live",
        allow_unknown_cost=False,
        cost_policy=policy,
        input_usd_per_million=input_rate,
        output_usd_per_million=output_rate,
    )
    return {
        "mode": "live",
        "opt_in": True,
        "split": split,
        "limit": len(selected),
        "single_pass": single,
        "tool_agent": tools,
        "cost_policy": policy,
        "note": "Live only when the command is --live. A missing price follows CIR_COST_POLICY and is not stored as zero.",
    }


def live_status() -> dict:
    if os.environ.get("CIR_EVAL_LIVE") != "1":
        return {
            "status": "pending",
            "reason": "CIR_EVAL_LIVE is not 1. No live model call was made.",
            "cost_usd": None,
        }
    if not os.environ.get("CIR_LLM_BASE_URL") or not os.environ.get("CIR_LLM_MODEL"):
        return {
            "status": "pending",
            "reason": "CIR_EVAL_LIVE=1 but CIR_LLM_BASE_URL or CIR_LLM_MODEL is empty.",
            "cost_usd": None,
        }
    return {
        "status": "configured",
        "reason": "Credentials are present. Run `python evaluations/run_baseline.py --live` to call the provider. The default command does not.",
        "cost_usd": None,
    }
