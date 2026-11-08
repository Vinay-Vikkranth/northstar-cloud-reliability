"""Cost policy. A missing price is never stored as zero."""

import json

from recovery_api.agent import run_model_loop
from recovery_api.investigate import _live_agent
from evaluations.runners import run_live_evaluation


def _tool_then_diagnosis(messages):
    assistant = sum(1 for item in messages if item.get("role") == "assistant")
    usage = {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
    if assistant == 0:
        return json.dumps({"type": "tool", "name": "get_source_customer", "arguments": {}}), usage
    return json.dumps(
        {
            "type": "diagnosis",
            "cause": "missing_update",
            "observations": ["The records disagree."],
            "evidence_ids": ["ev-source"],
            "hypotheses": [],
            "missing_information": [],
            "next_action": "propose_repair",
            "candidate_repair_type": "plan_sync",
        }
    ), usage


def _loop(policy: str, **extra):
    calls = []

    def apply_tool(name, arguments):
        calls.append(name)
        return {"has_link": True, "source": {"status": "ok", "plan": "premium", "evidence_id": "ev-source"}}, None

    result = run_model_loop(
        tool_names=["get_source_customer"],
        observation={"has_link": True},
        completer=_tool_then_diagnosis,
        apply_tool=apply_tool,
        known_ids=lambda obs: {"ev-source"} if (obs.get("source") or {}).get("evidence_id") else set(),
        budget={
            "max_tool_calls": 4,
            "max_tokens": 8000,
            "max_elapsed_seconds": 10,
            "max_estimated_cost_usd": 0.05,
            "cost_policy": policy,
            **extra,
        },
        scope={},
    )
    return result, calls


def test_local_policy_completes_without_a_price_and_strict_rejects():
    local, local_calls = _loop("local_unmeasured")
    assert local_calls == ["get_source_customer"]
    assert local["model"]["cause"] == "missing_update"
    assert local["usage"]["cost_usd"] is None
    assert local["usage"]["cost_known"] is False
    assert local["usage"]["cost_accounting"] == "unmeasured"
    assert local["usage"]["calls"] == 2

    strict, strict_calls = _loop("strict")
    assert strict_calls == []
    assert strict["error"] == "budget_exhausted"
    assert strict["reason"] == "cost_unknown"
    assert strict["usage"]["cost_usd"] is None
    assert strict["usage"]["calls"] == 1
    assert "CIR_COST_POLICY" in strict["detail"]


def test_local_policy_still_enforces_the_token_ceiling():
    def huge(_messages):
        return json.dumps({"type": "tool", "name": "get_source_customer", "arguments": {}}), {"total_tokens": 10000}

    result = run_model_loop(
        tool_names=["get_source_customer"],
        observation={"has_link": True},
        completer=huge,
        apply_tool=lambda name, arguments: ({}, None),
        known_ids=lambda obs: set(),
        budget={"max_tokens": 10, "max_elapsed_seconds": 5, "max_estimated_cost_usd": 1, "cost_policy": "local_unmeasured"},
        scope={},
    )
    assert result["reason"] == "tokens"
    assert result["usage"]["cost_usd"] is None


def test_estimated_policy_labels_the_estimate_and_stops_when_unconfigured():
    under, calls = _loop("estimated", input_usd_per_million=1, output_usd_per_million=2)
    assert calls == ["get_source_customer"]
    assert under["usage"]["cost_accounting"] == "estimate"
    assert under["usage"]["cost_known"] is False
    assert under["usage"]["cost_usd"] > 0
    assert "not a provider charge" in under["usage"]["cost_note"]

    over, over_calls = _loop("estimated", input_usd_per_million=1_000_000, output_usd_per_million=1_000_000)
    assert over_calls == []
    assert over["reason"] == "cost"
    assert over["usage"]["cost_accounting"] == "estimate"
    assert over["usage"]["cost_usd"] > 0.05

    missing, missing_calls = _loop("estimated")
    assert missing_calls == []
    assert missing["reason"] == "cost_unconfigured"
    assert missing["usage"]["cost_usd"] is None


def test_live_agent_uses_the_configured_local_policy(monkeypatch):
    calls = []

    monkeypatch.setattr("recovery_api.investigate.run_link_id", lambda db, run: "link-1")

    def apply_tool(db, run, name, obs, arguments=None):
        calls.append(name)
        return {
            **obs,
            "has_link": True,
            "source": {"status": "ok", "plan": "premium", "revision": 4, "evidence_id": "ev-source", "freshness": "established"},
            "destination": {"status": "ok", "plan": "basic", "revision": 2, "evidence_id": "ev-dest", "freshness": "established"},
        }

    monkeypatch.setattr("recovery_api.investigate._apply_tool", apply_tool)

    class Settings:
        cir_max_tool_calls = 4
        cir_max_tokens = 8000
        cir_max_elapsed_seconds = 10

        def cost_controls(self):
            return {
                "max_estimated_cost_usd": 0.05,
                "cost_policy": "local_unmeasured",
                "input_usd_per_million": 0,
                "output_usd_per_million": 0,
            }

    monkeypatch.setattr("recovery_api.investigate.get_settings", lambda: Settings())
    run = type("Run", (), {})()
    run.usage = {}
    run.tool_trace = []
    run.budget = {}
    run.id = "run"
    run.workspace_id = "ws"
    run.incident_id = "inc"
    result = _live_agent(db=None, run=run, obs={"has_link": True}, completer=_tool_then_diagnosis)
    assert calls == ["compare_records", "list_sync_attempts", "get_source_customer"]
    assert "diagnosis" in result
    assert result["usage"]["cost_usd"] is None
    assert result["usage"]["cost_accounting"] == "unmeasured"

    calls.clear()

    class Strict(Settings):
        def cost_controls(self):
            return {**Settings.cost_controls(self), "cost_policy": "strict"}

    monkeypatch.setattr("recovery_api.investigate.get_settings", lambda: Strict())
    run.budget = {}
    run.tool_trace = []
    stopped = _live_agent(db=None, run=run, obs={"has_link": True}, completer=_tool_then_diagnosis)
    # Required local reads precede the first model call; unknown model price
    # still blocks all subsequent model-selected tools and proposals.
    assert calls == ["compare_records", "list_sync_attempts"]
    assert stopped["reason"] == "cost_unknown"
    assert stopped["usage"]["cost_usd"] is None


def test_live_evaluation_follows_the_same_policy():
    case = {
        "id": "mismatch",
        "customer_link_id": "link-alex",
        "observation": {
            "has_link": True,
            "source": {"status": "ok", "plan": "premium", "revision": 4, "evidence_id": "src-mismatch"},
            "destination": {"status": "ok", "plan": "basic", "revision": 2, "evidence_id": "dst-mismatch"},
            "attempts": [],
        },
        "label": {"cause": "missing_update", "next_action": "propose_repair", "repair_allowed": True},
    }

    def completer(messages):
        blob = json.dumps(messages)
        usage = {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8}
        if sum(1 for item in messages if item.get("role") == "assistant") == 0 and "premium" not in blob:
            return json.dumps({"type": "tool", "name": "compare_records", "arguments": {}}), usage
        return json.dumps(
            {
                "type": "diagnosis",
                "cause": "missing_update",
                "observations": ["plans differ"],
                "evidence_ids": ["src-mismatch", "dst-mismatch"],
                "hypotheses": [],
                "missing_information": [],
                "next_action": "propose_repair",
                "candidate_repair_type": "plan_sync",
            }
        ), usage

    local = run_live_evaluation([case], completer, split="varied", seed=1, limit=1, cost_policy="local_unmeasured")
    assert local["cost_policy"] == "local_unmeasured"
    assert local["tool_agent"]["cost_known"] is False
    assert local["tool_agent"]["cost_usd"] is None
    assert local["tool_agent"]["diagnosis_correct"] == 1, local["tool_agent"]["confusion"]

    strict = run_live_evaluation([case], completer, split="varied", seed=1, limit=1, cost_policy="strict")
    assert strict["tool_agent"]["diagnosis_correct"] == 0
    assert strict["tool_agent"]["cost_known"] is False
    assert strict["tool_agent"]["cost_usd"] is None
