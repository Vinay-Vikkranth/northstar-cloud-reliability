import json

import httpx
import pytest

from recovery_api.hubspot import HUBSPOT_ORIGIN, HubSpotClient, HubSpotError


def _transport(handler):
    return httpx.MockTransport(handler)


def test_pagination_rate_limit_and_unexpected_payload():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"errorType": "RATE_LIMIT"})
        if request.url.params.get("after"):
            return httpx.Response(200, json={"results": [{"id": "2", "properties": {}}], "paging": {}})
        return httpx.Response(
            200,
            json={"results": [{"id": "1", "properties": {}}], "paging": {"next": {"after": "2"}}},
        )

    client = HubSpotClient("token", plan_property="northstar_plan", transport=_transport(handler), sleeper=lambda _seconds: None)
    page = client.list_contact_page(limit=100)
    assert page["next_after"] == "2"
    assert client.list_contact_page(after="2")["results"][0]["id"] == "2"
    assert calls["n"] == 3

    def bad(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    broken = HubSpotClient("token", plan_property="northstar_plan", transport=_transport(bad), sleeper=lambda _seconds: None)
    with pytest.raises(HubSpotError) as raised:
        broken.list_contact_page()
    assert raised.value.code == "malformed"


def test_contact_read_uses_only_id_plan_and_modified_time():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token"
        assert "email" not in request.url.params["properties"]
        assert request.url.path == "/crm/v3/objects/contacts/42"
        return httpx.Response(
            200,
            json={
                "id": "42",
                "properties": {"northstar_plan": "Premium", "lastmodifieddate": "2026-11-02T00:00:00Z", "email": "hidden@example.com"},
            },
        )

    client = HubSpotClient("token", plan_property="northstar_plan", transport=_transport(handler))
    record = client.get_contact("42")
    assert record["plan"] == "premium"
    assert record["freshness"] == "established"
    assert record["modified_at"] == "2026-11-02T00:00:00Z"
    assert "email" not in record


def test_unauthorized_and_origin_lock():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"category": "EXPIRED_AUTHENTICATION"})

    client = HubSpotClient("expired", plan_property="northstar_plan", transport=_transport(handler))
    with pytest.raises(HubSpotError) as raised:
        client.get_contact("42")
    assert raised.value.code == "unauthorized"
    with pytest.raises(HubSpotError):
        HubSpotClient("token", plan_property="northstar_plan", base_url="https://evil.example")
    assert HUBSPOT_ORIGIN == "https://api.hubapi.com"


def test_missing_modified_time_is_not_fresh():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "9", "properties": {"northstar_plan": "basic"}})

    client = HubSpotClient("token", plan_property="northstar_plan", transport=_transport(handler))
    record = client.get_contact("9")
    assert record["freshness"] == "unestablished"
    assert record["status"] == "malformed"
