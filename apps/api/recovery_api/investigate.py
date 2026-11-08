"""LangGraph investigation. Checkpoints never authorize a write."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from recovery_api.agent import run_model_loop
from recovery_api.db import session_factory
from recovery_api.models import (
    ConnectorConfig,
    CustomerLink,
    Diagnosis,
    Incident,
    InvestigationRun,
    Proposal,
    RunbookDoc,
    SyncAttempt,
)
from recovery_api.records import add_evidence, audit, link_in_workspace, read_crm, read_support, utcnow
from recovery_api.settings import get_settings
from recovery_core import WORKFLOW_VERSION
from recovery_core.diagnosis import diagnose, text_looks_untrusted
from recovery_core.proposals import build_repair_proposal, isoformat_utc
from recovery_core.redact import sanitize

TOOL_NAMES = {
    "get_source_customer",
    "get_destination_customer",
    "list_sync_attempts",
    "get_connector_health",
    "search_runbooks",
    "compare_records",
}


def _trace(run: InvestigationRun, name: str, evidence_id: str | None) -> None:
    trace = list(run.tool_trace or [])
    trace.append({"tool": name, "evidence_id": evidence_id, "at": utcnow().isoformat()})
    run.tool_trace = trace


def _runbook_query(obs: dict) -> str:
    source = obs.get("source") or {}
    dest = obs.get("destination") or {}
    if text_looks_untrusted(source.get("notes")):
        return "untrusted imported instructions"
    if source.get("status") == "unauthorized":
        return "credentials reconnect access"
    if source.get("status") in {"missing_field", "unmapped"}:
        return "mapping field version"
    if source.get("plan") and dest.get("plan") and source.get("plan") != dest.get("plan"):
        return "missing synchronization replay"
    return "reconciliation duplicate ordering"


def _search(db: Session, query: str, limit: int = 3) -> list[dict]:
    limit = min(max(limit, 1), 5)
    rows = db.execute(
        text(
            """
            SELECT id::text, slug, version, title, body
            FROM runbook_docs
            WHERE to_tsvector('english', title || ' ' || body) @@ plainto_tsquery('english', :query)
            ORDER BY ts_rank(to_tsvector('english', title || ' ' || body), plainto_tsquery('english', :query)) DESC
            LIMIT :limit
            """
        ),
        {"query": query[:200], "limit": limit},
    ).mappings()
    return [
        {
            "id": row["id"],
            "slug": row["slug"],
            "version": row["version"],
            "title": row["title"],
            "body": row["body"][:1500],
            "content_trust": "untrusted",
        }
        for row in rows
    ]


def _parse_search(arguments: dict | None, obs: dict) -> tuple[str | None, int | None, str | None]:
    """Accept an optional query and a limit from 1 to 5. Anything else is rejected."""
    arguments = arguments or {}
    if "query" in arguments and arguments.get("query") is not None:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            return None, None, "invalid_query"
        query = query.strip()
    else:
        query = _runbook_query(obs)
    raw_limit = arguments.get("limit", 3)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return None, None, "invalid_limit"
    if limit < 1 or limit > 5:
        return None, None, "invalid_limit"
    return query, limit, None


def _apply_tool(db: Session, run: InvestigationRun, name: str, obs: dict, arguments: dict | None = None) -> dict:
    if name not in TOOL_NAMES:
        _trace(run, f"rejected:{name}", None)
        return obs
    link = link_in_workspace(db, run.workspace_id, run_link_id(db, run))
    if name == "get_connector_health":
        statuses = _connector_health(db, link)
        evidence = add_evidence(
            db,
            workspace_id=run.workspace_id,
            investigation_run_id=run.id,
            source="connector_health",
            content=statuses,
        )
        obs = {**obs, "health": statuses, "health_evidence_id": str(evidence.id)}
        _trace(run, name, str(evidence.id))
        return obs
    if link is None:
        obs = {**obs, "has_link": False}
        _trace(run, name, None)
        return obs
    if name == "get_source_customer":
        obs = {**obs, "has_link": True, "source": read_crm(db, link, investigation_id=run.id)}
        _trace(run, name, obs["source"].get("evidence_id"))
        return obs
    if name == "get_destination_customer":
        obs = {**obs, "has_link": True, "destination": read_support(db, link, investigation_id=run.id)}
        _trace(run, name, obs["destination"].get("evidence_id"))
        return obs
    if name == "list_sync_attempts":
        rows = db.scalars(
            select(SyncAttempt)
            .where(SyncAttempt.workspace_id == run.workspace_id, SyncAttempt.customer_link_id == link.id)
            .order_by(SyncAttempt.created_at.desc())
            .limit(20)
        ).all()
        attempts = []
        evidence_ids = []
        for row in rows:
            evidence = add_evidence(
                db,
                workspace_id=run.workspace_id,
                investigation_run_id=run.id,
                source="sync_attempt",
                content={
                    "state": row.state,
                    "source_revision": row.source_revision,
                    "attempt_count": row.attempt_count,
                    "external_event_id": row.external_event_id,
                    "response": row.sanitized_response,
                },
                revision=str(row.source_revision) if row.source_revision is not None else None,
            )
            evidence_ids.append(str(evidence.id))
            attempts.append(
                {
                    "state": row.state,
                    "source_revision": row.source_revision,
                    "attempt_count": row.attempt_count,
                    "evidence_id": str(evidence.id),
                }
            )
        obs = {**obs, "attempts": attempts}
        _trace(run, name, evidence_ids[0] if evidence_ids else None)
        return obs
    if name == "compare_records":
        source = read_crm(db, link, investigation_id=run.id)
        dest = read_support(db, link, investigation_id=run.id, source_name="support_compare")
        evidence = add_evidence(
            db,
            workspace_id=run.workspace_id,
            investigation_run_id=run.id,
            source="compare",
            content={
                "source_plan": source.get("plan"),
                "destination_plan": dest.get("plan"),
                "source_status": source.get("status"),
                "destination_status": dest.get("status"),
                "plans_differ": source.get("plan") != dest.get("plan"),
            },
        )
        obs = {**obs, "source": source, "destination": dest, "compare_evidence_id": str(evidence.id)}
        _trace(run, name, str(evidence.id))
        return obs
    if name == "search_runbooks":
        query, limit, error = _parse_search(arguments, obs)
        if error:
            _trace(run, "rejected:search_runbooks", None)
            return obs
        found = _search(db, query or "", limit or 3)
        evidence = add_evidence(
            db,
            workspace_id=run.workspace_id,
            investigation_run_id=run.id,
            source="runbook",
            content={"documents": found, "content_trust": "untrusted"},
        )
        documents = [
            {
                "id": item["id"],
                "evidence_id": str(evidence.id),
                "title": item["title"],
                "version": item["version"],
                "content": item["body"][:1500],
                "content_trust": "untrusted",
            }
            for item in found
        ]
        obs = {
            **obs,
            "runbook_evidence_ids": [str(evidence.id)],
            "runbook_documents": documents,
            "runbook_text": "\n".join(item["content"] for item in documents),
        }
        _trace(run, name, str(evidence.id))
        return obs
    return obs


def run_link_id(db: Session, run: InvestigationRun):
    incident = db.get(Incident, run.incident_id)
    if incident is None or incident.workspace_id != run.workspace_id:
        return None
    return incident.customer_link_id


def _known_evidence_ids(obs: dict) -> set[str]:
    found = set()
    for key in ("source", "destination"):
        if (obs.get(key) or {}).get("evidence_id"):
            found.add(obs[key]["evidence_id"])
    for attempt in obs.get("attempts") or []:
        if attempt.get("evidence_id"):
            found.add(attempt["evidence_id"])
    found.update(obs.get("runbook_evidence_ids") or [])
    if obs.get("health_evidence_id"):
        found.add(obs["health_evidence_id"])
    if obs.get("compare_evidence_id"):
        found.add(obs["compare_evidence_id"])
    return found


def _finish_observation(obs: dict) -> dict:
    source = obs.get("source") or {}
    untrusted = text_looks_untrusted(source.get("notes"), obs.get("runbook_text"))
    return {**obs, "untrusted_text_present": untrusted, "has_link": bool(obs.get("has_link"))}


def _connector_health(db: Session, link: CustomerLink | None) -> dict:
    """Report the selected source connector. A HubSpot source is not described as the demo CRM."""
    import httpx

    settings = get_settings()
    statuses: dict[str, str] = {}
    source = db.get(ConnectorConfig, link.source_connector_id) if link is not None else None
    if source is not None and source.provider == "hubspot":
        statuses["crm"] = "configured" if settings.hubspot_token else "not_configured"
        statuses["crm_auth"] = "present" if settings.hubspot_token else "missing"
        statuses["crm_provider"] = "hubspot"
    else:
        try:
            response = httpx.get(f"{settings.cir_crm_base_url}/health/ready", timeout=3.0)
            statuses["crm"] = "ok" if response.status_code == 200 else "down"
        except httpx.HTTPError:
            statuses["crm"] = "down"
        statuses["crm_provider"] = source.provider if source is not None else "unselected"
    try:
        response = httpx.get(f"{settings.cir_support_base_url}/health/ready", timeout=3.0)
        statuses["support"] = "ok" if response.status_code == 200 else "down"
    except httpx.HTTPError:
        statuses["support"] = "down"
    return statuses


def _live_agent(db: Session, run: InvestigationRun, obs: dict, completer) -> dict:
    settings = get_settings()
    link_id = run_link_id(db, run)

    def apply_tool(name: str, arguments: dict) -> tuple[dict, str | None]:
        if name not in TOOL_NAMES:
            _trace(run, f"rejected:{name}", None)
            return obs_box[0], "tool_not_allowed"
        requested = arguments.get("customer_link_id")
        if requested and str(requested) != str(link_id):
            _trace(run, f"rejected:{name}", None)
            return obs_box[0], "wrong_workspace"
        if name == "search_runbooks":
            _query, _limit, error = _parse_search(arguments, obs_box[0])
            if error:
                _trace(run, "rejected:search_runbooks", None)
                return obs_box[0], error
        obs_box[0] = _apply_tool(db, run, name, obs_box[0], arguments)
        return obs_box[0], None

    obs_box = [dict(obs)]
    result = run_model_loop(
        tool_names=sorted(TOOL_NAMES),
        observation=obs_box[0],
        completer=completer,
        apply_tool=apply_tool,
        known_ids=_known_evidence_ids,
        budget={
            "max_tool_calls": settings.cir_max_tool_calls,
            "max_tokens": settings.cir_max_tokens,
            "max_elapsed_seconds": settings.cir_max_elapsed_seconds,
            **settings.cost_controls(),
            **(run.budget or {}),
        },
        scope={"workspace_id": str(run.workspace_id), "incident_id": str(run.incident_id), "customer_link_id": str(link_id) if link_id else None},
        prerequisite_tools=(["compare_records", "list_sync_attempts"]
                            if link_id and not (obs.get("source") and obs.get("destination")) else []),
    )
    usage = result.get("usage") or {}
    run.usage = {**(run.usage or {}), **usage, "usage_source": usage.get("cost_accounting") or "unmeasured"}
    observation = _finish_observation(result.get("observation") or obs_box[0])
    if result.get("error"):
        return {"error": result["error"], "reason": result.get("reason"), "observation": observation, "usage": result.get("usage") or {}}
    parsed = result["model"]
    diagnosis = diagnose(observation)
    explanation = " ".join(parsed.get("observations") or [])
    diagnosis["competing_explanations"] = list(diagnosis.get("competing_explanations") or []) + [
        {
            "cause": parsed.get("cause"),
            "reason": f"Live model hypothesis. It does not authorize a write. {explanation}".strip(),
        }
    ]
    diagnosis["limitations"] = list(diagnosis.get("limitations") or []) + list(parsed.get("missing_information") or [])
    diagnosis["model_explanation"] = explanation
    return {"diagnosis": diagnosis, "observation": observation, "model": parsed, "usage": result.get("usage") or {}}


def _run_is_inactive(db: Session, investigation_id) -> bool:
    run = db.scalar(
        select(InvestigationRun)
        .where(InvestigationRun.id == investigation_id)
        .execution_options(populate_existing=True)
    )
    if run is None or run.state in {"CANCELLED", "COMPLETED"}:
        return True
    incident = db.scalar(
        select(Incident).where(Incident.id == run.incident_id).execution_options(populate_existing=True)
    )
    return incident is None or incident.active_investigation_id != run.id


def _persist_conclusion(db: Session, run: InvestigationRun, obs: dict, diagnosis: dict) -> dict:
    run = db.scalar(select(InvestigationRun).where(InvestigationRun.id == run.id).with_for_update())
    if run is None or run.state == "CANCELLED":
        db.rollback()
        return {"proposal_id": None, "proposal_hash": None, "summary": "", "stopped": True}
    incident = db.scalar(select(Incident).where(Incident.id == run.incident_id).with_for_update())
    if incident is None or incident.active_investigation_id != run.id:
        if run.state not in {"COMPLETED", "AWAITING_APPROVAL", "CANCELLED"}:
            run.state = "CANCELLED"
            run.finished_at = utcnow()
            db.commit()
        else:
            db.rollback()
        return {"proposal_id": None, "proposal_hash": None, "summary": run.summary, "stopped": True}
    existing = db.scalar(select(Diagnosis).where(Diagnosis.investigation_run_id == run.id))
    if existing is not None:
        proposal = db.scalar(select(Proposal).where(Proposal.investigation_run_id == run.id))
        return {
            "proposal_id": str(proposal.id) if proposal else None,
            "proposal_hash": proposal.proposal_hash if proposal else None,
            "summary": run.summary,
        }
    incident = db.get(Incident, run.incident_id)
    link = link_in_workspace(db, run.workspace_id, incident.customer_link_id if incident else None)
    row = Diagnosis(
        workspace_id=run.workspace_id,
        investigation_run_id=run.id,
        cause=diagnosis["cause"],
        evidence_ids=diagnosis.get("evidence_ids") or [],
        competing=diagnosis.get("competing_explanations") or [],
        limitations=" ".join(diagnosis.get("limitations") or []),
        next_action=diagnosis["next_action"],
        certainty=diagnosis["certainty"],
        observations=diagnosis.get("observations") or [],
        created_at=utcnow(),
    )
    db.add(row)
    proposal_id = None
    proposal_hash = None
    proposal_body = None
    if link is not None and diagnosis.get("repair_allowed"):
        proposal_body = build_repair_proposal(
            workspace_id=str(run.workspace_id),
            incident_id=str(run.incident_id),
            customer_link_id=str(link.id),
            destination_connector_id=str(link.destination_connector_id),
            diagnosis=diagnosis,
            source=obs.get("source") or {},
            destination=obs.get("destination") or {},
            mapping_version=link.mapping_version,
            ttl_minutes=get_settings().cir_proposal_ttl_minutes,
        )
    if proposal_body is not None:
        proposal = Proposal(
            workspace_id=run.workspace_id,
            incident_id=run.incident_id,
            investigation_run_id=run.id,
            customer_link_id=link.id,
            connector_id=link.destination_connector_id,
            field_name="plan",
            before_value=proposal_body["before"],
            after_value=proposal_body["after"],
            source_preconditions=proposal_body["source_preconditions"],
            destination_preconditions=proposal_body["destination_preconditions"],
            mapping_version=proposal_body["mapping_version"],
            evidence_ids=proposal_body["evidence_ids"],
            expires_at=datetime.fromisoformat(proposal_body["expires_at"]),
            proposal_hash=proposal_body["proposal_hash"],
            version=1,
            decision_state="open",
            created_at=utcnow(),
        )
        db.add(proposal)
        db.flush()
        proposal_id = str(proposal.id)
        proposal_hash = proposal.proposal_hash
        incident.state = "AWAITING_APPROVAL"
        run.state = "AWAITING_APPROVAL"
        run.summary = _summary(incident, link, diagnosis, proposal_body)
    else:
        incident.state = _state_for(diagnosis)
        run.state = "COMPLETED"
        run.summary = _summary(incident, link, diagnosis, None)
        run.finished_at = utcnow()
    incident.updated_at = utcnow()
    audit(
        db,
        workspace_id=run.workspace_id,
        action="investigation_concluded",
        resource_type="investigation",
        resource_id=str(run.id),
        metadata={"cause": diagnosis["cause"], "next_action": diagnosis["next_action"], "mode": run.mode},
        trace_id=run.trace_id,
    )
    db.commit()
    return {"proposal_id": proposal_id, "proposal_hash": proposal_hash, "summary": run.summary}


def _state_for(diagnosis: dict) -> str:
    if diagnosis["cause"] in {"no_mismatch", "duplicate_event", "out_of_order_event"} and diagnosis["next_action"] == "no_action":
        return "RESOLVED"
    if diagnosis["next_action"] == "escalate" or diagnosis["certainty"] == "conflicting":
        return "ESCALATED"
    return "NEEDS_INFORMATION"


def _summary(incident: Incident, link: CustomerLink | None, diagnosis: dict, proposal: dict | None) -> str:
    name = link.display_name if link else "the reported customer"
    observations = " ".join(diagnosis.get("observations") or [])
    if proposal:
        return (
            f"{name} needs a reviewed plan change from {proposal['before']} to {proposal['after']}. "
            f"{observations} The write is not applied until an approver accepts this exact proposal."
        )
    return f"{name}: {diagnosis['cause'].replace('_', ' ')}. {observations}"


def _db():
    return session_factory()()


def prepare(state: dict) -> dict:
    db = _db()
    try:
        run = db.get(InvestigationRun, state["investigation_id"])
        incident = db.get(Incident, run.incident_id)
        has_link = incident.customer_link_id is not None
        if run.mode == "live":
            pending = ["live_agent"]
        elif has_link:
            pending = [
                "get_connector_health",
                "get_source_customer",
                "get_destination_customer",
                "list_sync_attempts",
                "compare_records",
                "search_runbooks",
            ]
        else:
            pending = ["get_connector_health"]
        return {"pending": pending, "tool_calls": 0, "observation": {"has_link": has_link}, "done": False}
    finally:
        db.close()


def tool_step(state: dict) -> dict:
    db = _db()
    try:
        run = db.get(InvestigationRun, state["investigation_id"])
        if _run_is_inactive(db, run.id):
            return {"pending": [], "observation": state.get("observation") or {}, "stopped": True, "tool_calls": state.get("tool_calls") or 0}
        if run.mode == "live":
            from recovery_api.llm import OpenAICompatibleCompleter

            settings = get_settings()
            completer = state.get("completer")
            if completer is None:
                if not settings.cir_llm_base_url or not settings.cir_llm_model:
                    return {"pending": [], "error": "provider_unavailable", "tool_calls": 1}
                completer = OpenAICompatibleCompleter(
                    settings.cir_llm_base_url,
                    settings.cir_llm_api_key,
                    settings.cir_llm_model,
                    settings.cir_llm_timeout_seconds,
                )
            result = _live_agent(db, run, state.get("observation") or {}, completer)
            db.commit()
            if _run_is_inactive(db, run.id):
                return {
                    "pending": [],
                    "observation": result.get("observation") or {},
                    "stopped": True,
                    "tool_calls": len(run.tool_trace or []),
                }
            return {
                "pending": [],
                "observation": result.get("observation") or {},
                "diagnosis": result.get("diagnosis"),
                "error": result.get("error"),
                "tool_calls": len(run.tool_trace or []),
            }
        pending = list(state.get("pending") or [])
        name = pending.pop(0)
        obs = _apply_tool(db, run, name, dict(state.get("observation") or {}))
        calls = int(state.get("tool_calls") or 0) + 1
        if calls > get_settings().cir_max_tool_calls:
            pending = []
        db.commit()
        if _run_is_inactive(db, run.id):
            return {"pending": [], "observation": obs, "stopped": True, "tool_calls": calls}
        return {"pending": pending, "observation": obs, "tool_calls": calls}
    finally:
        db.close()


def conclude(state: dict) -> dict:
    db = _db()
    try:
        run = db.get(InvestigationRun, state["investigation_id"])
        if state.get("stopped") or _run_is_inactive(db, run.id):
            return {"done": True, "proposal_id": None, "stopped": True}
        if state.get("error"):
            diagnosis = {
                "cause": "insufficient_evidence",
                "next_action": "escalate",
                "certainty": "incomplete",
                "evidence_ids": [],
                "competing_explanations": [],
                "limitations": [f"The investigator stopped: {state['error']}."],
                "observations": ["No repair was proposed because the investigator did not return a valid result."],
                "repair_allowed": False,
            }
            obs = state.get("observation") or {"has_link": False}
        else:
            obs = _finish_observation(state.get("observation") or {})
            diagnosis = state.get("diagnosis") or diagnose(obs)
            if diagnosis.get("repair_allowed") and (obs.get("source") or {}).get("freshness") == "unestablished":
                diagnosis = {
                    **diagnosis,
                    "repair_allowed": False,
                    "next_action": "escalate",
                    "limitations": list(diagnosis.get("limitations") or [])
                    + ["Freshness could not be established, so no repair was proposed."],
                }
        saved = _persist_conclusion(db, run, obs, diagnosis)
        return {**saved, "done": True, "diagnosis": diagnosis}
    finally:
        db.close()


def wait_for_human(state: dict) -> dict:
    decision = interrupt(
        {
            "proposal_id": state.get("proposal_id"),
            "proposal_hash": state.get("proposal_hash"),
            "note": "Checkpoint only. Approval in the domain table is what allows a repair.",
        }
    )
    db = _db()
    try:
        run = db.get(InvestigationRun, state["investigation_id"])
        if isinstance(decision, dict):
            run.summary = (run.summary or "") + " A human decision was recorded on the checkpoint."
        db.commit()
    finally:
        db.close()
    return {"human_decision": decision if isinstance(decision, dict) else {"seen": True}}


def route_pending(state: dict) -> str:
    if state.get("pending"):
        return "tool"
    return "conclude"


def route_done(state: dict) -> str:
    if state.get("proposal_id"):
        return "wait"
    return "end"


class InvState(TypedDict, total=False):
    investigation_id: str
    workspace_id: str
    incident_id: str
    mode: str
    pending: list
    tool_calls: int
    observation: dict
    diagnosis: dict
    proposal_id: str | None
    proposal_hash: str | None
    summary: str
    done: bool
    error: str | None
    human_decision: dict | None


def build_graph(checkpointer):
    graph = StateGraph(InvState)
    graph.add_node("prepare", prepare)
    graph.add_node("tool", tool_step)
    graph.add_node("conclude", conclude)
    graph.add_node("wait", wait_for_human)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges("prepare", route_pending, {"tool": "tool", "conclude": "conclude"})
    graph.add_conditional_edges("tool", route_pending, {"tool": "tool", "conclude": "conclude"})
    graph.add_conditional_edges("conclude", route_done, {"wait": "wait", "end": END})
    graph.add_edge("wait", END)
    return graph.compile(checkpointer=checkpointer)


def run_investigation(investigation_id: str) -> None:
    settings = get_settings()
    db = _db()
    try:
        run = db.scalar(select(InvestigationRun).where(InvestigationRun.id == investigation_id).with_for_update())
        if run is None or run.state in {"COMPLETED", "CANCELLED", "AWAITING_APPROVAL"}:
            return
        incident = db.scalar(select(Incident).where(Incident.id == run.incident_id).with_for_update())
        if incident is None or incident.active_investigation_id != run.id:
            run.state = "CANCELLED"
            run.finished_at = utcnow()
            db.commit()
            return
        run.state = "RUNNING"
        db.commit()
        workspace_id = str(run.workspace_id)
        incident_id = str(run.incident_id)
        mode = run.mode
    finally:
        db.close()
    config = {"configurable": {"thread_id": investigation_id}}
    with PostgresSaver.from_conn_string(settings.psycopg_url) as saver:
        app = build_graph(saver)
        app.invoke(
            {
                "investigation_id": investigation_id,
                "workspace_id": workspace_id,
                "incident_id": incident_id,
                "mode": mode,
                "pending": [],
                "tool_calls": 0,
                "observation": {},
            },
            config,
        )


def resume_checkpoint(investigation_id: str, decision: dict) -> None:
    """Record the human decision in the graph. This function does not repair anything."""
    settings = get_settings()
    from langgraph.types import Command

    config = {"configurable": {"thread_id": investigation_id}}
    with PostgresSaver.from_conn_string(settings.psycopg_url) as saver:
        app = build_graph(saver)
        app.invoke(Command(resume=sanitize(decision)), config)
