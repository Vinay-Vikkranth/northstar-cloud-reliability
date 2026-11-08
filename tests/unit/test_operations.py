from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import logging
import pytest
from opentelemetry import trace
from recovery_api.operations import percentile, summarize
from recovery_api.settings import Settings
from recovery_api.telemetry import JsonLogFormatter, configure_logging, tracer

def job(state="done", duration=10, expired=False):
    return SimpleNamespace(state=state,last_duration_ms=duration,lease_expires=datetime.now(timezone.utc)-timedelta(seconds=10) if expired else None)

def test_percentiles_are_interpolated_and_empty_is_unknown():
    assert percentile([], .95) is None
    assert percentile([1,2,3,4],.5)==2.5
    assert percentile([1,2,3,4],.95)==3.85

def test_slo_needs_samples_and_pending_is_not_terminal_failure():
    result=summarize([job("pending"),job()])
    assert result["slo"]["status"]=="insufficient_data"
    assert result["completion_rate"]==1
    assert summarize([])["completion_rate"] is None

def test_dead_and_stale_lease_raise_alerts_even_before_slo_threshold():
    result=summarize([job("dead"),job("leased",expired=True)])
    assert {a["code"] for a in result["alerts"]}=={"dead_letter","expired_lease"}

def test_measured_slo_failure_is_visible():
    result=summarize([job(duration=6000) for _ in range(10)])
    assert result["slo"]["status"]=="breached"
    assert result["p95_ms"]==6000

def test_production_fails_closed_on_defaults():
    with pytest.raises(ValueError,match="secure cookies"):
        Settings(cir_environment="production",_env_file=None).validate_runtime()

def test_invalid_environment_is_rejected():
    with pytest.raises(ValueError,match="CIR_ENVIRONMENT"):
        Settings(cir_environment="prodution",_env_file=None).validate_runtime()

def test_production_rejects_reused_connector_and_administrator_secrets():
    settings=Settings(cir_environment="production",cir_cookie_secure=True,cir_cors_origins="https://northstar.example.com",cir_database_url="postgresql+psycopg://user:strong@postgres:5432/cir",crm_read_token="a"*40,support_token="a"*40,crm_admin_token="b"*40,support_admin_token="c"*40,cir_webhook_secret="d"*40,_env_file=None)
    with pytest.raises(ValueError,match="separate injected secrets"):settings.validate_runtime()

def test_actual_span_context_is_present_in_json_log():
    configure_logging()
    with tracer().start_as_current_span("correlation-proof") as span:
        row=json.loads(JsonLogFormatter().format(logging.LogRecord("test",logging.INFO,"",1,"safe operation",(),None)))
        assert row["trace_id"]==format(span.get_span_context().trace_id,"032x")
        assert row["span_id"]==format(span.get_span_context().span_id,"016x")

def test_failed_request_keeps_trace_id_and_hides_exception_details():
    from fastapi.routing import APIRoute
    from fastapi.testclient import TestClient
    from recovery_api.main import app
    configure_logging()
    async def fail():raise RuntimeError("password=private-test-value")
    route=APIRoute("/__failure_observability_test",fail,methods=["GET"])
    app.router.routes.insert(0,route)
    try:
        response=TestClient(app).get("/__failure_observability_test")
        assert response.status_code==500
        assert len(response.headers["x-trace-id"])==32
        assert "private-test-value" not in response.text
        assert response.json()["error"]["request_id"]==response.headers["x-request-id"]
    finally:app.router.routes.remove(route)
