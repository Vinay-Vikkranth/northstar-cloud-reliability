import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from recovery_api.models import LoginAttempt, Membership, SessionToken, User
from recovery_api.settings import get_settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def client_key(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def too_many_failures(db: Session, key: str) -> bool:
    cutoff = utcnow() - timedelta(minutes=15)
    rows = db.scalars(
        select(LoginAttempt).where(
            LoginAttempt.client_key == key,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.created_at >= cutoff,
        )
    ).all()
    return len(rows) >= 5


def record_login(db: Session, key: str, succeeded: bool) -> None:
    db.add(LoginAttempt(client_key=key, succeeded=succeeded, created_at=utcnow()))


def create_session(db: Session, user: User) -> tuple[str, str]:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db.add(
        SessionToken(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(hours=settings.cir_session_hours),
        )
    )
    return token, csrf


def user_from_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = db.scalar(select(SessionToken).where(SessionToken.token_hash == hash_token(token)))
    if row is None or row.revoked_at is not None or row.expires_at < utcnow():
        return None
    user = db.get(User, row.user_id)
    if user is None or user.disabled:
        return None
    return user


def revoke_token(db: Session, token: str | None) -> None:
    if not token:
        return
    row = db.scalar(select(SessionToken).where(SessionToken.token_hash == hash_token(token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()


def membership_for(db: Session, user: User, workspace_id) -> Membership | None:
    return db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == workspace_id)
    )


_IMPLIES = {
    "viewer": {"viewer"},
    "operator": {"viewer", "operator"},
    "approver": {"viewer", "approver"},
    "administrator": {"viewer", "operator", "approver", "administrator"},
}


def has_role(membership: Membership, *roles: str) -> bool:
    granted: set[str] = set()
    for role in membership.roles or []:
        granted.update(_IMPLIES.get(role, {role}))
    return bool(granted.intersection(roles))
