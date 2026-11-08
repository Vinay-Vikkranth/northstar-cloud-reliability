"""Destination service with conditional writes and idempotency receipts."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from recovery_core import PLAN_VALUES
from recovery_core.hashing import canonical_hash

DATABASE_URL = os.environ.get(
    "SUPPORT_DATABASE_URL",
    "postgresql+psycopg://support_app:support_app_local@127.0.0.1:55438/support",
)
TOKEN = os.environ.get("SUPPORT_TOKEN", "support-local")
ADMIN_TOKEN = os.environ.get("SUPPORT_ADMIN_TOKEN", "support-admin-local")


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source_revision_applied: Mapped[int | None] = mapped_column(Integer, nullable=True)
    write_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Receipt(Base):
    __tablename__ = "receipts"
    idempotency_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Faults(Base):
    __tablename__ = "faults"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outage: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limit: Mapped[bool] = mapped_column(Boolean, default=False)
    write_rate_limit: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_after_seconds: Mapped[int] = mapped_column(Integer, default=1)
    drop_response_after_commit: Mapped[bool] = mapped_column(Boolean, default=False)


engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
app = FastAPI(title="Northstar support service", version="0.1.0")
from recovery_api.telemetry import install_service_observability
install_service_observability(app, "northstar-support-simulator")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def init_db() -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE faults ADD COLUMN IF NOT EXISTS write_rate_limit boolean NOT NULL DEFAULT false"))
    with SessionLocal() as session:
        if session.get(Faults, 1) is None:
            session.add(Faults(id=1))
            session.commit()


@app.on_event("startup")
def startup() -> None:
    init_db()


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def require_token(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def require_admin(x_demo_admin_token: str | None = Header(default=None)) -> None:
    if not x_demo_admin_token or x_demo_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="admin token required")


class AccountIn(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    display_name: str = Field(max_length=200)
    plan: str
    revision: int = 1
    source_revision_applied: int | None = None


class ResetIn(BaseModel):
    accounts: list[AccountIn]


class PatchIn(BaseModel):
    plan: str = Field(max_length=40)
    expected_revision: int
    source_revision: int | None = None


class FaultPatch(BaseModel):
    outage: bool | None = None
    rate_limit: bool | None = None
    write_rate_limit: bool | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0, le=60)
    drop_response_after_commit: bool | None = None


def view(row: Account) -> dict:
    return {
        "id": row.id,
        "display_name": row.display_name,
        "plan": row.plan,
        "revision": row.revision,
        "source_revision_applied": row.source_revision_applied,
        "write_count": row.write_count,
        "updated_at": row.updated_at.astimezone(timezone.utc).isoformat(),
    }


@app.get("/health/live")
def live() -> dict:
    return {"status": "live"}


@app.get("/health/ready")
def ready() -> dict:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.post("/admin/reset", dependencies=[Depends(require_admin)])
def reset(payload: ResetIn, session: Session = Depends(get_session)) -> dict:
    session.query(Receipt).delete()
    session.query(Account).delete()
    now = utcnow()
    for item in payload.accounts:
        if item.plan not in PLAN_VALUES:
            raise HTTPException(status_code=422, detail="plan must be basic, premium, or enterprise")
        session.add(
            Account(
                id=item.id,
                display_name=item.display_name,
                plan=item.plan,
                revision=item.revision,
                source_revision_applied=item.source_revision_applied,
                write_count=0,
                updated_at=now,
            )
        )
    faults = session.get(Faults, 1)
    faults.outage = False
    faults.rate_limit = False
    faults.write_rate_limit = False
    faults.drop_response_after_commit = False
    faults.retry_after_seconds = 1
    session.commit()
    return {"accounts": len(payload.accounts)}


@app.patch("/admin/faults", dependencies=[Depends(require_admin)])
def patch_faults(payload: FaultPatch, session: Session = Depends(get_session)) -> dict:
    faults = session.get(Faults, 1)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(faults, key, value)
    session.commit()
    return {"updated": True}


@app.get("/admin/faults", dependencies=[Depends(require_admin)])
def get_faults(session: Session = Depends(get_session)) -> dict:
    faults = session.get(Faults, 1)
    return {
        "outage": faults.outage,
        "rate_limit": faults.rate_limit,
        "write_rate_limit": faults.write_rate_limit,
        "retry_after_seconds": faults.retry_after_seconds,
        "drop_response_after_commit": faults.drop_response_after_commit,
    }


@app.get("/v1/accounts/{account_id}", dependencies=[Depends(require_token)])
def get_account(account_id: str, session: Session = Depends(get_session)) -> dict:
    faults = session.get(Faults, 1)
    if faults.outage:
        raise HTTPException(status_code=503, detail="unavailable")
    if faults.rate_limit:
        raise HTTPException(status_code=429, detail="rate limited", headers={"Retry-After": str(faults.retry_after_seconds)})
    row = session.get(Account, account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return view(row)


@app.get("/v1/receipts/{idempotency_key}", dependencies=[Depends(require_token)])
def get_receipt(idempotency_key: str, session: Session = Depends(get_session)) -> dict:
    row = session.get(Receipt, idempotency_key)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "idempotency_key": row.idempotency_key,
        "payload_hash": row.payload_hash,
        "status_code": row.status_code,
        "response": row.response_body,
    }


@app.patch("/v1/accounts/{account_id}", dependencies=[Depends(require_token)])
def patch_account(
    account_id: str,
    payload: PatchIn,
    session: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    if not idempotency_key or len(idempotency_key) > 120:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    if payload.plan not in PLAN_VALUES:
        raise HTTPException(status_code=422, detail="plan is not writable")
    if if_match is not None and if_match != str(payload.expected_revision):
        raise HTTPException(status_code=412, detail="If-Match does not match expected revision")
    canonical = {
        "account_id": account_id,
        "plan": payload.plan,
        "expected_revision": payload.expected_revision,
        "source_revision": payload.source_revision,
    }
    payload_hash = canonical_hash(canonical)
    existing = session.scalar(select(Receipt).where(Receipt.idempotency_key == idempotency_key).with_for_update())
    if existing is not None:
        if existing.payload_hash != payload_hash:
            return JSONResponse(status_code=409, content={"error": "idempotency_conflict"})
        return JSONResponse(status_code=existing.status_code, content=existing.response_body)

    faults = session.get(Faults, 1)
    if faults.outage:
        return JSONResponse(status_code=503, content={"error": "unavailable"})
    if faults.rate_limit or faults.write_rate_limit:
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited"},
            headers={"Retry-After": str(faults.retry_after_seconds)},
        )

    account = session.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    if account.revision != payload.expected_revision:
        body = {
            "error": "conflict",
            "revision": account.revision,
            "plan": account.plan,
            "receipt_id": idempotency_key,
        }
        session.add(
            Receipt(
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                status_code=409,
                response_body=body,
                created_at=utcnow(),
            )
        )
        session.commit()
        return JSONResponse(status_code=409, content=body)

    account.plan = payload.plan
    account.revision += 1
    account.write_count += 1
    if payload.source_revision is not None:
        account.source_revision_applied = payload.source_revision
    account.updated_at = utcnow()
    body = {
        "id": account.id,
        "plan": account.plan,
        "revision": account.revision,
        "source_revision_applied": account.source_revision_applied,
        "write_count": account.write_count,
        "receipt_id": idempotency_key,
    }
    session.add(
        Receipt(
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            status_code=200,
            response_body=body,
            created_at=account.updated_at,
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        stored = session.get(Receipt, idempotency_key)
        if stored is None:
            raise
        if stored.payload_hash != payload_hash:
            return JSONResponse(status_code=409, content={"error": "idempotency_conflict"})
        return JSONResponse(status_code=stored.status_code, content=stored.response_body)
    if faults.drop_response_after_commit:
        # The success receipt is already committed. The caller sees an unfinished write
        # and must recover from the receipt instead of sending a different payload.
        return JSONResponse(status_code=500, content={"error": "response_lost_after_commit"})
    return JSONResponse(status_code=200, content=body)
