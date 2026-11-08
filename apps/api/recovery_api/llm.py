"""One OpenAI-compatible chat adapter. Offline mode never constructs this client."""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from recovery_core.diagnosis import CAUSES, NEXT_ACTIONS


class ProviderUnavailable(RuntimeError):
    pass


class ModelDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause: str
    observations: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    next_action: str
    candidate_repair_type: str


def parse_model_diagnosis(payload: Any) -> ModelDiagnosis:
    if isinstance(payload, str):
        payload = json.loads(payload)
    model = ModelDiagnosis.model_validate(payload)
    if model.cause not in CAUSES or model.next_action not in NEXT_ACTIONS:
        raise ValidationError.from_exception_data("ModelDiagnosis", [])
    if model.candidate_repair_type not in {"plan_sync", "none"}:
        raise ValidationError.from_exception_data("ModelDiagnosis", [])
    return model


class OpenAICompatibleCompleter:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30) -> None:
        if not base_url or not model:
            raise ProviderUnavailable("live model is not configured")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._response_tokens: int | None = None
        self._request_timeout: float | None = None

    def limit_response(self, *, max_tokens: int, timeout: float) -> None:
        """Bound the next request. This does not undo usage already consumed."""
        self._response_tokens = max(1, int(max_tokens))
        self._request_timeout = max(0.1, min(self.timeout, float(timeout)))

    def __call__(self, messages: list[dict[str, str]]) -> tuple[str, dict]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        if self._response_tokens is not None:
            body["max_tokens"] = self._response_tokens
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                timeout=self._request_timeout or self.timeout,
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable("timeout") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailable("transport") from exc
        if response.status_code == 401:
            raise ProviderUnavailable("unauthorized")
        if response.status_code == 429:
            raise ProviderUnavailable("rate_limited")
        if response.status_code >= 500:
            raise ProviderUnavailable("unavailable")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderUnavailable("malformed") from exc
        usage = body.get("usage") or {}
        cost = usage.get("cost_usd")
        if cost is None and isinstance(body.get("cost_usd"), (int, float)):
            cost = body.get("cost_usd")
        return content, {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cost_usd": cost,
            "cost_note": "Provider cost is recorded only when the response includes it. None means not supplied.",
        }
