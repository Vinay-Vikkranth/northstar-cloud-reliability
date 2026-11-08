import re
import time
from typing import Callable

import httpx

from recovery_core.backoff import retry_delay_seconds

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class ClientError(Exception):
    def __init__(self, status: int, body: dict | None = None, retry_after: str | None = None) -> None:
        self.status = status
        self.body = body or {}
        self.retry_after = retry_after
        super().__init__(f"http {status}")


def _check_id(value: str) -> str:
    if not ID_RE.match(value):
        raise ValueError("identifier is not allowed")
    return value


class ServiceClient:
    """HTTP client pinned to one configured origin. Callers cannot pass a URL."""

    def __init__(self, base_url: str, token: str, timeout: float = 5.0, sleeper: Callable[[float], None] = time.sleep) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.sleeper = sleeper

    def request(self, method: str, path: str, *, headers: dict | None = None, json_body: dict | None = None, allow_retry: bool = False) -> httpx.Response:
        if method not in {"GET", "PATCH", "POST"}:
            raise ValueError("method is not allowed")
        if not path.startswith("/") or "://" in path or ".." in path:
            raise ValueError("path is not allowed")
        attempt = 1
        while True:
            from opentelemetry.propagate import inject
            carrier = {}
            inject(carrier)
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    method,
                    self.base_url + path,
                    headers={"Authorization": f"Bearer {self.token}", **carrier, **(headers or {})},
                    json=json_body,
                )
            if response.status_code != 429 or not allow_retry or attempt >= 3:
                return response
            attempt += 1
            self.sleeper(retry_delay_seconds(response.headers.get("Retry-After"), attempt=attempt))

    def get_json(self, path: str) -> tuple[int, dict, str | None]:
        try:
            response = self.request("GET", path)
        except httpx.TimeoutException as exc:
            raise ClientError(599, {"error": "timeout"}) from exc
        except httpx.TransportError as exc:
            raise ClientError(598, {"error": "transport"}) from exc
        body = response.json() if response.content else {}
        if not isinstance(body, dict):
            body = {"raw_type": type(body).__name__}
        return response.status_code, body, response.headers.get("Retry-After")


def crm_customer(client: ServiceClient, customer_id: str) -> tuple[int, dict]:
    status, body, _retry = client.get_json(f"/v1/customers/{_check_id(customer_id)}")
    return status, body


def support_account(client: ServiceClient, account_id: str) -> tuple[int, dict]:
    status, body, retry_after = client.get_json(f"/v1/accounts/{_check_id(account_id)}")
    if status == 429:
        body = {**body, "retry_after": retry_after}
    return status, body


def support_receipt(client: ServiceClient, key: str) -> tuple[int, dict]:
    if not re.match(r"^[A-Za-z0-9_.:-]{1,120}$", key):
        raise ValueError("idempotency key is not allowed")
    status, body, _retry = client.get_json(f"/v1/receipts/{key}")
    return status, body


def support_patch_plan(
    client: ServiceClient,
    account_id: str,
    *,
    plan: str,
    expected_revision: int,
    source_revision: int | None,
    idempotency_key: str,
) -> tuple[int, dict]:
    if plan not in {"basic", "premium", "enterprise"}:
        raise ValueError("plan is not writable")
    path = f"/v1/accounts/{_check_id(account_id)}"
    body = {"plan": plan, "expected_revision": expected_revision, "source_revision": source_revision}
    headers = {"Idempotency-Key": idempotency_key, "If-Match": str(expected_revision)}
    try:
        response = client.request("PATCH", path, headers=headers, json_body=body, allow_retry=False)
    except httpx.TimeoutException as exc:
        raise ClientError(599, {"error": "timeout"}) from exc
    except httpx.TransportError as exc:
        raise ClientError(598, {"error": "transport"}) from exc
    try:
        payload = response.json() if response.content else {}
    except Exception:
        payload = {"error": "unreadable_body"}
    if not isinstance(payload, dict):
        payload = {"error": "unexpected_body"}
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        payload = {**payload, "retry_after": retry_after}
    return response.status_code, payload
