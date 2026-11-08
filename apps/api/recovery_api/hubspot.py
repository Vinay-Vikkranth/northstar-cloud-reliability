"""HubSpot CRM read adapter. Writes are not sent to HubSpot."""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx

from recovery_core.backoff import retry_delay_seconds
from recovery_core.hashing import fingerprint_record
from recovery_core.mapping import normalize_plan

HUBSPOT_ORIGIN = "https://api.hubapi.com"


class HubSpotError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code)


class HubSpotClient:
    def __init__(
        self,
        token: str,
        *,
        plan_property: str,
        base_url: str = HUBSPOT_ORIGIN,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        timeout: float = 10.0,
    ) -> None:
        if base_url.rstrip("/") != HUBSPOT_ORIGIN and transport is None:
            raise HubSpotError(0, "origin_not_allowed")
        if not plan_property or not plan_property.replace("_", "").isalnum():
            raise HubSpotError(0, "invalid_plan_property")
        self.plan_property = plan_property
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        self.sleeper = sleeper

    def close(self) -> None:
        self._client.close()

    def _send(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        attempt = 1
        while True:
            response = self._client.request(method, path, params=params)
            if response.status_code != 429 or attempt >= 3:
                return response
            attempt += 1
            self.sleeper(retry_delay_seconds(response.headers.get("Retry-After"), attempt=attempt))

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        if not str(contact_id).isdigit():
            raise HubSpotError(400, "invalid_contact_id")
        properties = f"{self.plan_property},lastmodifieddate"
        try:
            response = self._send(
                "GET",
                f"/crm/v3/objects/contacts/{contact_id}",
                params={"properties": properties, "archived": "false"},
            )
        except httpx.HTTPError as exc:
            raise HubSpotError(599, "unavailable") from exc
        return self._interpret_contact(response)

    def list_contact_page(self, *, limit: int = 100, after: str | None = None) -> dict[str, Any]:
        """Contract helper. The smoke command does not call this."""
        limit = min(max(limit, 1), 100)
        params: dict[str, Any] = {
            "limit": limit,
            "archived": "false",
            "properties": f"{self.plan_property},lastmodifieddate",
        }
        if after:
            params["after"] = after
        response = self._send("GET", "/crm/v3/objects/contacts", params=params)
        if response.status_code == 401:
            raise HubSpotError(401, "unauthorized")
        if response.status_code == 429:
            raise HubSpotError(429, "rate_limited")
        if response.status_code >= 500:
            raise HubSpotError(response.status_code, "unavailable")
        try:
            body = response.json()
        except Exception as exc:
            raise HubSpotError(response.status_code, "malformed") from exc
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise HubSpotError(response.status_code, "malformed")
        paging = body.get("paging") or {}
        next_after = None
        if isinstance(paging, dict):
            nxt = paging.get("next") or {}
            if isinstance(nxt, dict):
                next_after = nxt.get("after")
        return {"results": body["results"], "next_after": next_after}

    def _interpret_contact(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 401:
            raise HubSpotError(401, "unauthorized")
        if response.status_code == 404:
            raise HubSpotError(404, "not_found")
        if response.status_code == 429:
            raise HubSpotError(429, "rate_limited")
        if response.status_code >= 500:
            raise HubSpotError(response.status_code, "unavailable")
        try:
            body = response.json()
        except Exception as exc:
            raise HubSpotError(response.status_code, "malformed") from exc
        if not isinstance(body, dict) or not body.get("id"):
            raise HubSpotError(response.status_code, "malformed")
        properties = body.get("properties") if isinstance(body.get("properties"), dict) else {}
        raw_plan = properties.get(self.plan_property)
        modified = properties.get("lastmodifieddate")
        canonical, error = normalize_plan(raw_plan) if raw_plan is not None else (None, "missing_field")
        freshness = "established" if modified else "unestablished"
        status = "ok"
        if error == "missing_field":
            status = "missing_field"
        elif error == "unmapped":
            status = "unmapped"
        if freshness == "unestablished" and status == "ok":
            status = "malformed"
        return {
            "provider": "hubspot",
            "status": status,
            "http_status": response.status_code,
            "id": str(body["id"]),
            "plan": canonical,
            "raw_plan": raw_plan,
            "revision": None,
            "modified_at": modified,
            "fingerprint": fingerprint_record(
                record_id=str(body["id"]),
                plan=canonical,
                revision=modified,
                modified_at=modified if isinstance(modified, str) else None,
            ),
            "freshness": freshness if status == "ok" else "unestablished",
            "notes": None,
        }
