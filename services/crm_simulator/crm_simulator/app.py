"""Synthetic CRM. Fault switches exist only on the admin API, never on customer reads."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from recovery_core.signing import sign_body

DATABASE_URL = os.environ.get(
    "CRM_DATABASE_URL",
    "postgresql+psycopg://crm_app:crm_app_local@127.0.0.1:55438/crm",
)
READ_TOKEN = os.environ.get("CRM_READ_TOKEN", "crm-read-local")
ADMIN_TOKEN = os.environ.get("CRM_ADMIN_TOKEN", "crm-admin-local")
WEBHOOK_SECRET = os.environ.get("CIR_WEBHOOK_SECRET", "webhook-local-dev")
WEBHOOK_URL = os.environ.get("CIR_EVENT_URL", "http://127.0.0.1:28601/api/v1/events/crm-simulator")


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Faults(Base):
    __tablename__ = "faults"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    credentials_expired: Mapped[bool] = mapped_column(Boolean, default=False)
    outage: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limit: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_after_seconds: Mapped[int] = mapped_column(Integer, default=1)
    rename_plan_field: Mapped[bool] = mapped_column(Boolean, default=False)
    drop_events: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_events: Mapped[bool] = mapped_column(Boolean, default=False)


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(80), unique=True)
    customer_id: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    plan: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def init_db() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        if session.get(Faults, 1) is None:
            session.add(Faults(id=1))
            session.commit()


class CustomerIn(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    display_name: str = Field(max_length=200)
    plan: str | None = Field(default=None, max_length=80)
    revision: int = 1
    notes: str = Field(default="", max_length=2000)


class CustomerPatch(BaseModel):
    plan: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=2000)
    display_name: str | None = Field(default=None, max_length=200)


class FaultPatch(BaseModel):
    credentials_expired: bool | None = None
    outage: bool | None = None
    rate_limit: bool | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0, le=60)
    rename_plan_field: bool | None = None
    drop_events: bool | None = None
    duplicate_events: bool | None = None


class ResetIn(BaseModel):
    customers: list[CustomerIn]


def require_admin(x_demo_admin_token: str | None = Header(default=None)) -> None:
    if not x_demo_admin_token or x_demo_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="admin token required")


def require_reader(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {READ_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


def public_customer(row: Customer, faults: Faults) -> dict:
    body = {
        "id": row.id,
        "display_name": row.display_name,
        "revision": row.revision,
        "updated_at": row.updated_at.astimezone(timezone.utc).isoformat(),
        "notes": row.notes,
    }
    if faults.rename_plan_field:
        body["subscription_tier"] = row.plan
    else:
        body["plan"] = row.plan
    return body


app = FastAPI(title="Northstar CRM simulator", version="0.1.0")
from recovery_api.telemetry import install_service_observability
install_service_observability(app, "northstar-crm-simulator")


@app.on_event("startup")
def startup() -> None:
    init_db()


def db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@app.get("/health/live")
def live() -> dict:
    return {"status": "live"}


@app.get("/health/ready")
def ready() -> dict:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/v1/customers")
def list_customers(
    limit: int = 20,
    cursor: str | None = None,
    session: Session = Depends(db),
    _: None = Depends(require_reader),
) -> dict:
    faults = session.get(Faults, 1)
    _guard_read(faults)
    limit = min(max(limit, 1), 100)
    stmt = select(Customer).order_by(Customer.id)
    if cursor:
        stmt = stmt.where(Customer.id > cursor)
    rows = list(session.scalars(stmt.limit(limit + 1)))
    page = rows[:limit]
    next_cursor = page[-1].id if len(rows) > limit and page else None
    return {"items": [public_customer(row, faults) for row in page], "next_cursor": next_cursor}


@app.get("/v1/customers/{customer_id}")
def get_customer(
    customer_id: str,
    session: Session = Depends(db),
    _: None = Depends(require_reader),
) -> dict:
    faults = session.get(Faults, 1)
    _guard_read(faults)
    row = session.get(Customer, customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return public_customer(row, faults)


def _guard_read(faults: Faults) -> None:
    if faults.credentials_expired:
        raise HTTPException(status_code=401, detail="expired credentials")
    if faults.outage:
        raise HTTPException(status_code=503, detail="unavailable")
    if faults.rate_limit:
        raise HTTPException(
            status_code=429,
            detail="rate limited",
            headers={"Retry-After": str(faults.retry_after_seconds)},
        )


@app.post("/admin/reset", dependencies=[Depends(require_admin)])
def reset(payload: ResetIn, session: Session = Depends(db)) -> dict:
    session.query(Outbox).delete()
    session.query(Customer).delete()
    now = utcnow()
    for item in payload.customers:
        session.add(
            Customer(
                id=item.id,
                display_name=item.display_name,
                plan=item.plan,
                revision=item.revision,
                notes=item.notes,
                updated_at=now,
            )
        )
    faults = session.get(Faults, 1)
    for name in (
        "credentials_expired",
        "outage",
        "rate_limit",
        "rename_plan_field",
        "drop_events",
        "duplicate_events",
    ):
        setattr(faults, name, False)
    faults.retry_after_seconds = 1
    session.commit()
    return {"customers": len(payload.customers)}


@app.patch("/admin/customers/{customer_id}", dependencies=[Depends(require_admin)])
def patch_customer(customer_id: str, payload: CustomerPatch, session: Session = Depends(db)) -> dict:
    row = session.get(Customer, customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if payload.display_name is not None:
        row.display_name = payload.display_name
    if payload.notes is not None:
        row.notes = payload.notes
    if payload.plan is not None:
        row.plan = payload.plan
    row.revision += 1
    row.updated_at = utcnow()
    faults = session.get(Faults, 1)
    event_id = f"evt_{uuid.uuid4().hex[:16]}"
    status = "dropped" if faults.drop_events else "pending"
    session.add(
        Outbox(
            id=str(uuid.uuid4()),
            event_id=event_id,
            customer_id=row.id,
            revision=row.revision,
            plan=row.plan,
            status=status,
            created_at=row.updated_at,
        )
    )
    session.commit()
    return {"id": row.id, "revision": row.revision, "event_status": status}


@app.patch("/admin/faults", dependencies=[Depends(require_admin)])
def patch_faults(payload: FaultPatch, session: Session = Depends(db)) -> dict:
    faults = session.get(Faults, 1)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(faults, key, value)
    session.commit()
    return {"updated": True}


@app.post("/admin/flush", dependencies=[Depends(require_admin)])
def flush(session: Session = Depends(db)) -> dict:
    faults = session.get(Faults, 1)
    pending = list(session.scalars(select(Outbox).where(Outbox.status == "pending").order_by(Outbox.created_at)))
    sent = 0
    target = os.environ.get("CIR_EVENT_URL", WEBHOOK_URL)
    with httpx.Client(timeout=5.0) as client:
        for event in pending:
            body = {
                "event_id": event.event_id,
                "customer_id": event.customer_id,
                "revision": event.revision,
                "plan": event.plan,
                "occurred_at": event.created_at.astimezone(timezone.utc).isoformat(),
            }
            raw = __import__("json").dumps(body, separators=(",", ":")).encode()
            headers = {"X-Signature": sign_body(WEBHOOK_SECRET, raw), "Content-Type": "application/json"}
            copies = 2 if faults.duplicate_events else 1
            for _ in range(copies):
                response = client.post(target, content=raw, headers=headers)
                response.raise_for_status()
            event.status = "sent"
            sent += 1
    session.commit()
    return {"sent": sent}


@app.get("/admin/faults", dependencies=[Depends(require_admin)])
def get_faults(session: Session = Depends(db)) -> dict:
    faults = session.get(Faults, 1)
    return {
        "credentials_expired": faults.credentials_expired,
        "outage": faults.outage,
        "rate_limit": faults.rate_limit,
        "retry_after_seconds": faults.retry_after_seconds,
        "rename_plan_field": faults.rename_plan_field,
        "drop_events": faults.drop_events,
        "duplicate_events": faults.duplicate_events,
    }
