import json

import httpx
import pytest

from evaluations.run_baseline import main
from evaluations.runners import build_live_completer, live_status, run_live_evaluation, score_rules, single_pass, tool_agent


def _case(name: str, source_status: str, source_plan, dest_plan, cause: str, action: str, repair: bool) -> dict:
    return {
        "id": name,
        "customer_link_id": "link-alex",
        "observation": {
            "has_link": True,
            "source": {"status": source_status, "plan": source_plan, "revision": 4, "evidence_id": f"src-{name}"},
            "destination": {"status": "ok", "plan": dest_plan, "revision": 2, "source_revision_applied": 1, "evidence_id": f"dst-{name}"},
            "attempts": [],
        },
        "label": {"cause": cause, "next_action": action, "repair_allowed": repair},
    }


def test_fake_provider_sees_both_plans_and_live_is_pending(monkeypatch):
    monkeypatch.delenv("CIR_EVAL_LIVE", raising=False)
    assert live_status()["status"] == "pending"
    case = _case("mismatch", "ok", "premium", "basic", "missing_update", "propose_repair", True)
    seen = []

    def completer(messages):
        seen.append(json.dumps(messages))
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
        ), {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5, "cost_usd": None}

    predicted, usage, _latency, _messages = single_pass(case, completer)
    blob = seen[0]
    assert "premium" in blob and "basic" in blob
    assert "src-mismatch" in blob and "dst-mismatch" in blob
    assert "cause must be one of" in blob
    assert predicted["cause"] == "missing_update"
    assert usage["cost_usd"] is None

    calls = []

    def tool_completer(messages):
        calls.append(json.dumps(messages))
        blob = calls[-1]
        assistant = sum(1 for item in messages if item["role"] == "assistant")
        if assistant == 0:
            assert "premium" not in blob
            return json.dumps({"type": "tool", "name": "get_connector_health", "arguments": {}}), {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "cost_usd": 0,
            }
        if "unauthorized" in blob:
            return json.dumps(
                {
                    "type": "diagnosis",
                    "cause": "access_missing",
                    "observations": [],
                    "evidence_ids": [],
                    "hypotheses": [],
                    "missing_information": [],
                    "next_action": "reconnect",
                    "candidate_repair_type": "none",
                }
            ), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost_usd": 0}
        if "premium" not in blob:
            return json.dumps({"type": "tool", "name": "compare_records", "arguments": {}}), {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "cost_usd": 0,
            }
        return json.dumps(
            {
                "type": "diagnosis",
                "cause": "insufficient_evidence",
                "observations": [],
                "evidence_ids": ["forged"],
                "hypotheses": [],
                "missing_information": [],
                "next_action": "escalate",
                "candidate_repair_type": "none",
            }
        ), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost_usd": 0}

    mismatch = tool_agent(case, tool_completer)
    assert "premium" in calls[2]
    assert "forged" in calls[-1]
    auth = tool_agent(_case("auth", "unauthorized", None, "basic", "access_missing", "reconnect", False), tool_completer)
    assert mismatch[0]["tool_calls"] == ["get_connector_health", "compare_records"]
    assert auth[0]["tool_calls"] == ["get_connector_health"]
    assert mismatch[0]["evidence_ids"] == []

    scored = score_rules([case], split="contract", seed=1)
    assert scored["diagnosis_correct"] == 1
    assert scored["unsafe_repair_proposals"] == 0
    assert scored["citation_valid"] == 1


def test_rejected_tool_and_wrong_workspace_are_not_executed():
    def completer(messages):
        if sum(1 for item in messages if item["role"] == "assistant") == 0:
            return json.dumps({"type": "tool", "name": "write_plan", "arguments": {"customer_link_id": "other"}}), {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "cost_usd": 0,
            }
        return json.dumps(
            {
                "type": "diagnosis",
                "cause": "insufficient_evidence",
                "observations": [],
                "evidence_ids": ["src-mismatch"],
                "hypotheses": [],
                "missing_information": [],
                "next_action": "escalate",
                "candidate_repair_type": "none",
            }
        ), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost_usd": 0}

    case = _case("mismatch", "ok", "premium", "basic", "missing_update", "propose_repair", True)
    predicted, _usage, _latency, calls = tool_agent(case, completer)
    assert calls == ["write_plan"]
    assert predicted["cause"] == "insufficient_evidence"
    assert predicted["next_action"] == "escalate"


def test_tool_runner_retains_records_when_another_tool_is_read():
    case = _case("mismatch", "ok", "premium", "basic", "missing_update", "propose_repair", True)
    def completer(messages):
        turns = sum(m["role"] == "assistant" for m in messages)
        if turns < 2:
            payload = {"type": "tool", "name": ["compare_records", "list_sync_attempts"][turns], "arguments": {}}
        else:
            obs = json.loads(messages[-1]["content"])["observation"]
            assert obs["source"]["plan"] == "premium"
            assert obs["destination"]["plan"] == "basic"
            payload = {"type": "diagnosis", "cause": "missing_update", "observations": ["Plans differ"],
                       "evidence_ids": ["src-mismatch", "dst-mismatch"], "hypotheses": [],
                       "missing_information": [], "next_action": "propose_repair", "candidate_repair_type": "plan_sync"}
        return json.dumps(payload), {"total_tokens": 2, "cost_usd": 0}
    result, _, _, _ = tool_agent(case, completer)
    assert result["cause"] == "missing_update"
    assert result["loop_error"] is None


def test_live_evaluation_is_wired_and_stays_opt_in(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("the default path must not call a provider")

    monkeypatch.setattr(httpx, "post", explode)
    monkeypatch.delenv("CIR_EVAL_LIVE", raising=False)
    with pytest.raises(SystemExit):
        build_live_completer()
    monkeypatch.setenv("CIR_EVAL_LIVE", "1")
    monkeypatch.setenv("CIR_LLM_BASE_URL", "http://example.test/v1")
    monkeypatch.setenv("CIR_LLM_MODEL", "fake-local")
    adapter = build_live_completer()
    assert adapter.model == "fake-local"

    calls = {"n": 0}

    def completer(messages):
        calls["n"] += 1
        blob = json.dumps(messages)
        if calls["n"] == 1:
            assert "premium" in blob
        if sum(1 for item in messages if item.get("role") == "assistant") == 0 and "evidence_hidden" in blob:
            return json.dumps({"type": "tool", "name": "compare_records", "arguments": {}}), {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "cost_usd": 0,
            }
        return json.dumps(
            {
                "type": "diagnosis",
                "cause": "missing_update",
                "observations": ["synthetic"],
                "evidence_ids": ["src-mismatch", "dst-mismatch"],
                "hypotheses": [],
                "missing_information": [],
                "next_action": "propose_repair",
                "candidate_repair_type": "plan_sync",
            }
        ), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost_usd": 0}

    case = _case("mismatch", "ok", "premium", "basic", "missing_update", "propose_repair", True)
    result = run_live_evaluation([case], completer, split="varied", seed=1, limit=1)
    assert result["opt_in"] is True
    assert result["single_pass"]["sample_size"] == 1
    assert result["tool_agent"]["sample_size"] == 1
    assert calls["n"] >= 2
    routed = []
    monkeypatch.setattr("evaluations.run_baseline._run_live", lambda args: routed.append("live"))
    monkeypatch.setattr("evaluations.run_baseline._run_offline", lambda: routed.append("offline"))
    main([])
    main(["--live"])
    assert routed == ["offline", "live"]
