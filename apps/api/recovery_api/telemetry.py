import json
import logging
import os
import time
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from recovery_api.settings import get_settings
from recovery_core.redact import redact_text

_CONFIGURED = False
_FORBIDDEN = ("token", "secret", "password", "authorization", "cookie", "payload", "body", "notes")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the rendered line. Numeric %-placeholders stay numeric until formatting."""
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        record.msg = redact_text(rendered)
        record.args = ()
        return True


class JsonLogFormatter(logging.Formatter):
    """Escape messages and redact exception text before writing one JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "message": redact_text(record.getMessage()),
        }
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            payload["trace_id"] = format(context.trace_id, "032x")
            payload["span_id"] = format(context.span_id, "016x")
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class JsonlSpanExporter(SpanExporter):
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans) -> SpanExportResult:
        with self.path.open("a", encoding="utf-8") as handle:
            for span in spans:
                attributes = {}
                for key, value in (span.attributes or {}).items():
                    if any(word in key.lower() for word in _FORBIDDEN):
                        continue
                    attributes[key] = value if isinstance(value, (str, int, float, bool)) else str(value)[:200]
                handle.write(
                    json.dumps(
                        {
                            "name": span.name,
                            "trace_id": format(span.context.trace_id, "032x"),
                            "span_id": format(span.context.span_id, "016x"),
                            "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
                            "duration_ms": (span.end_time-span.start_time)/1_000_000,
                            "status": span.status.status_code.name,
                            "attributes": attributes,
                        }
                    )
                    + "\n"
                )
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def configure_logging() -> None:
    global _CONFIGURED
    settings = get_settings()
    Path(settings.cir_log_dir).mkdir(parents=True, exist_ok=True)
    path = (Path(settings.cir_log_dir) / "recovery.log").resolve()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(existing, logging.FileHandler) and Path(existing.baseFilename).resolve() == path for existing in root.handlers):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.addFilter(RedactingFilter())
        handler.setFormatter(JsonLogFormatter())
        root.addHandler(handler)
    if _CONFIGURED:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "northstar-recovery")}))
    provider.add_span_processor(BatchSpanProcessor(JsonlSpanExporter(settings.cir_trace_path)))
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)))
    trace.set_tracer_provider(provider)
    _CONFIGURED = True


def tracer():
    return trace.get_tracer("northstar.recovery")


def install_service_observability(app, service_name: str) -> None:
    """Trace propagation for the isolated HTTP simulators; never record bodies or auth."""
    from opentelemetry.propagate import extract
    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
    @app.on_event("startup")
    def setup_telemetry():
        configure_logging()
    @app.middleware("http")
    async def observe(request, call_next):
        started = time.perf_counter()
        with tracer().start_as_current_span(service_name + "." + request.method, context=extract(dict(request.headers))) as span:
            span.set_attribute("http.route", request.url.path)
            response = await call_next(request)
            span.set_attribute("http.status_code", response.status_code)
            logging.getLogger(service_name).info("connector method=%s status=%s duration_ms=%.2f", request.method, response.status_code, (time.perf_counter()-started)*1000)
            response.headers["X-Trace-ID"] = format(span.get_span_context().trace_id, "032x")
            return response
