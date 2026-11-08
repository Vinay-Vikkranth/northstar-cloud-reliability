"""Evidence-based diagnosis. This module never receives simulator fault names."""

from typing import Any

CAUSES = (
    "missing_update",
    "duplicate_event",
    "out_of_order_event",
    "rate_limited",
    "access_missing",
    "dependency_outage",
    "mapping_mismatch",
    "no_mismatch",
    "ambiguous_identity",
    "insufficient_evidence",
    "contradictory_evidence",
)

NEXT_ACTIONS = (
    "propose_repair",
    "no_action",
    "retry_later",
    "reconnect",
    "correct_mapping",
    "request_mapping",
    "escalate",
)

_INSTRUCTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "you are now",
    "system:",
    "reveal the",
    "disregard",
)


def text_looks_untrusted(*chunks: str | None) -> bool:
    blob = "\n".join(chunk.lower() for chunk in chunks if chunk)
    return any(marker in blob for marker in _INSTRUCTION_MARKERS)


def _ids(obs: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in ("source", "destination"):
        side = obs.get(key) or {}
        if side.get("evidence_id"):
            found.append(side["evidence_id"])
    for attempt in obs.get("attempts") or []:
        if attempt.get("evidence_id"):
            found.append(attempt["evidence_id"])
    for evidence_id in obs.get("runbook_evidence_ids") or []:
        found.append(evidence_id)
    if obs.get("health_evidence_id"):
        found.append(obs["health_evidence_id"])
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique = []
    for item in found:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _result(
    cause: str,
    next_action: str,
    certainty: str,
    evidence_ids: list[str],
    *,
    limitations: list[str],
    competing: list[dict[str, str]],
    observations: list[str],
) -> dict[str, Any]:
    if cause not in CAUSES:
        raise ValueError(cause)
    if next_action not in NEXT_ACTIONS:
        raise ValueError(next_action)
    return {
        "cause": cause,
        "next_action": next_action,
        "certainty": certainty,
        "evidence_ids": evidence_ids,
        "limitations": limitations,
        "competing_explanations": competing,
        "observations": observations,
        "repair_allowed": next_action == "propose_repair",
    }


def diagnose(obs: dict[str, Any]) -> dict[str, Any]:
    limitations: list[str] = []
    competing: list[dict[str, str]] = []
    if obs.get("untrusted_text_present"):
        limitations.append(
            "Some retrieved text contained instruction-like content. It was stored as evidence and was not followed."
        )
    evidence_ids = _ids(obs)
    if not obs.get("has_link"):
        return _result(
            "ambiguous_identity",
            "request_mapping",
            "incomplete",
            evidence_ids,
            limitations=limitations + ["No unique customer link is recorded for this report."],
            competing=competing,
            observations=["The incident does not point at one source and destination customer."],
        )

    source = obs.get("source") or {}
    dest = obs.get("destination") or {}
    attempts = list(obs.get("attempts") or [])
    observations = []

    if source.get("status") == "unauthorized":
        observations.append("The CRM refused the read with unauthorized status. No plan value was inferred.")
        return _result(
            "access_missing",
            "reconnect",
            "incomplete",
            evidence_ids,
            limitations=limitations + ["An authorized person must reconnect the CRM before another investigation."],
            competing=competing,
            observations=observations,
        )

    unavailable = {source.get("status"), dest.get("status")} & {"unavailable", "timeout"}
    if unavailable:
        observations.append("A required system did not return a usable record.")
        return _result(
            "dependency_outage",
            "retry_later",
            "incomplete",
            evidence_ids,
            limitations=limitations + ["No repair is proposed while a dependency is unavailable."],
            competing=competing,
            observations=observations,
        )

    if source.get("status") == "rate_limited" or dest.get("status") == "rate_limited":
        observations.append("A read was rate limited, so the current records are not both confirmed.")
        return _result(
            "rate_limited",
            "retry_later",
            "incomplete",
            evidence_ids,
            limitations=limitations + ["Wait for the published retry window before reading again."],
            competing=competing,
            observations=observations,
        )

    if source.get("status") in {"missing_field", "unmapped"}:
        observations.append("The source record did not yield a plan in the active mapping version.")
        return _result(
            "mapping_mismatch",
            "correct_mapping",
            "supported" if evidence_ids else "incomplete",
            evidence_ids,
            limitations=limitations
            + ["Publish an explicit mapping version, then investigate again. The current value was not rewritten."],
            competing=competing,
            observations=observations,
        )

    if source.get("status") != "ok" or dest.get("status") != "ok":
        observations.append("The source and destination were not both read successfully.")
        return _result(
            "insufficient_evidence",
            "escalate",
            "incomplete",
            evidence_ids,
            limitations=limitations + ["A repair requires a current read of both systems."],
            competing=competing,
            observations=observations,
        )

    source_plan = source.get("plan")
    dest_plan = dest.get("plan")
    observations.append(f"CRM plan is {source_plan} at revision {source.get('revision')}.")
    observations.append(
        f"Support plan is {dest_plan} at revision {dest.get('revision')} "
        f"(source revision applied: {dest.get('source_revision_applied')})."
    )
    plans_differ = source_plan != dest_plan
    source_rev = source.get("revision")
    applied = dest.get("source_revision_applied")
    duplicates = [item for item in attempts if item.get("state") in {"duplicate", "rejected_duplicate"}]
    stale = [item for item in attempts if item.get("state") in {"rejected_stale", "out_of_order"}]
    limited = [item for item in attempts if item.get("state") == "rate_limited"]

    if not plans_differ:
        if duplicates:
            observations.append("A repeated event was recorded and the plans already match.")
            if stale:
                competing.append(
                    {"cause": "out_of_order_event", "reason": "An older event was also rejected."}
                )
            return _result(
                "duplicate_event",
                "no_action",
                "supported",
                evidence_ids,
                limitations=limitations,
                competing=competing,
                observations=observations,
            )
        if stale:
            observations.append("An older event arrived after a newer revision and was not applied.")
            return _result(
                "out_of_order_event",
                "no_action",
                "supported",
                evidence_ids,
                limitations=limitations + ["An older event cannot overwrite the newer plan."],
                competing=competing,
                observations=observations,
            )
        observations.append("The mapped plans already match.")
        return _result(
            "no_mismatch",
            "no_action",
            "supported",
            evidence_ids,
            limitations=limitations,
            competing=competing,
            observations=observations,
        )

    if source_rev is not None and applied is not None and applied >= source_rev:
        observations.append(
            "The destination claims a source revision at least as new as the CRM, but the plans differ."
        )
        return _result(
            "contradictory_evidence",
            "escalate",
            "conflicting",
            evidence_ids,
            limitations=limitations
            + ["The records disagree in a way that a single plan write would not safely explain."],
            competing=[{"cause": "mapping_mismatch", "reason": "A mapping change could also present this way."}],
            observations=observations,
        )

    if limited:
        competing.append(
            {
                "cause": "missing_update",
                "reason": "Plans differ and the latest source revision has no successful apply.",
            }
        )
        highest = max(int(item.get("attempt_count") or 1) for item in limited)
        observations.append(f"Recent sync attempts were rate limited (highest attempt count {highest}).")
        if highest < 3:
            return _result(
                "rate_limited",
                "retry_later",
                "supported",
                evidence_ids,
                limitations=limitations + ["Automatic retries stay inside the backoff limit."],
                competing=competing,
                observations=observations,
            )
        limitations.append(
            "Backoff attempts are exhausted. A person may approve one repair; this is not another tight retry."
        )
        return _result(
            "rate_limited",
            "propose_repair",
            "supported",
            evidence_ids,
            limitations=limitations,
            competing=competing,
            observations=observations,
        )

    if stale:
        competing.append(
            {
                "cause": "out_of_order_event",
                "reason": "An older event was rejected, separately from the current difference.",
            }
        )
    observations.append("The latest CRM revision is not reflected on the support account.")
    return _result(
        "missing_update",
        "propose_repair",
        "supported",
        evidence_ids,
        limitations=limitations,
        competing=competing,
        observations=observations,
    )
