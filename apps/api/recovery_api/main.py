import secrets
import uuid
import logging
import time
from opentelemetry.propagate import extract
from opentelemetry.trace import Status, StatusCode

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from recovery_api.routes import router
from recovery_api.operations import router as operations_router
from recovery_api.settings import get_settings
from recovery_api.telemetry import configure_logging, tracer
from recovery_core.redact import redact_text

app = FastAPI(title="Northstar Recovery", version="0.1.0")
app.include_router(router)
app.include_router(operations_router)


@app.on_event("startup")
def startup() -> None:
    get_settings().validate_runtime()
    configure_logging()


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not _csrf_exempt(request):
            cookie = request.cookies.get("cir_csrf")
            header = request.headers.get("x-csrf-token")
            if not cookie or not header or not secrets.compare_digest(cookie, header):
                return JSONResponse(
                    status_code=403,
                    content={"error": {"code": "csrf", "message": "CSRF check failed", "request_id": request_id}},
                )
            origin = request.headers.get("origin")
            if origin and origin not in get_settings().cors_origins:
                return JSONResponse(
                    status_code=403,
                    content={"error": {"code": "origin", "message": "Origin is not allowed", "request_id": request_id}},
                )
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > 65536:
        return JSONResponse(
            status_code=413,
            content={"error": {"code": "too_large", "message": "Request body is too large", "request_id": request_id}},
        )
    start = time.perf_counter()
    with tracer().start_as_current_span(f"{request.method} {request.url.path}", context=extract(dict(request.headers))) as span:
        span.set_attribute("http.method", request.method)
        span.set_attribute("http.route", request.url.path)
        span.set_attribute("workspace_id", request.headers.get("x-workspace-id") or "")
        try:
            response = await call_next(request)
        except Exception as exc:
            span.record_exception(Exception(redact_text(str(exc))))
            span.set_status(Status(StatusCode.ERROR))
            logging.getLogger("recovery.http").exception("request failed request_id=%s",request_id)
            return JSONResponse(
                status_code=500,
                content={"error": {"code": "internal", "message": "The request could not be completed", "request_id": request_id}},
                headers={"X-Request-ID":request_id,"X-Trace-ID":format(span.get_span_context().trace_id,"032x")},
            )
        span.set_attribute("http.status_code", response.status_code)
        logging.getLogger("recovery.http").info("request method=%s route=%s status=%s duration_ms=%.2f request_id=%s", request.method, request.url.path, response.status_code, (time.perf_counter()-start)*1000, request_id)
        response.headers["X-Trace-ID"] = format(span.get_span_context().trace_id, "032x")
    response.headers["X-Request-ID"] = request_id
    return response


def _csrf_exempt(request: Request) -> bool:
    path = request.url.path
    return path == "/api/v1/auth/login" or path.startswith("/api/v1/events/")


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        body = detail
    else:
        body = {"code": "error", "message": redact_text(str(detail))}
    body["request_id"] = getattr(request.state, "request_id", None)
    return JSONResponse(status_code=exc.status_code, content={"error": body})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "invalid_request",
                "message": "The request was not valid",
                "fields": [item.get("loc", [])[-1] for item in exc.errors()][:8],
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


@app.get("/health/live")
def live() -> dict:
    from recovery_api.build_info import BUILD_ID

    return {"status": "live", "build": BUILD_ID}


@app.get("/health/ready")
def ready() -> dict:
    from sqlalchemy import text

    from recovery_api.db import get_engine

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "build": _build_id()})
    return {"status": "ready", "build": _build_id()}


def _build_id() -> str:
    from recovery_api.build_info import BUILD_ID

    return BUILD_ID


def _cors() -> None:
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Workspace-Id", "X-Request-ID"],
    )


_cors()

# The production image builds React and serves it on the same authenticated origin.
from pathlib import Path
from fastapi.staticfiles import StaticFiles
_web = Path(__file__).resolve().parents[2] / "web" / "dist"
if _web.exists():
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
