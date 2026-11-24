from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.auth import AuthSession, AuthUser

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def _as_utc(value: datetime) -> datetime:
    # SQLite commonly returns timezone-naive values even for timezone=True columns.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def verify_browser_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in settings.allowed_browser_origins:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Untrusted request origin.")


def require_session(request: Request, db: Session = Depends(get_db)) -> AuthUser:
    verify_browser_origin(request)
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    token_hash = digest_token(raw_token)
    session_record = db.get(AuthSession, token_hash)
    now = utc_now()

    if session_record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid or expired.")

    absolute_expired = _as_utc(session_record.expires_at) <= now
    idle_expired = _as_utc(session_record.last_activity_at) <= now - timedelta(
        minutes=settings.session_idle_minutes
    )
    if absolute_expired or idle_expired:
        db.delete(session_record)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid or expired.")

    user = db.get(AuthUser, session_record.user_id)
    if user is None or not user.is_active:
        db.delete(session_record)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid or expired.")

    if request.method.upper() in UNSAFE_METHODS:
        csrf_cookie = request.cookies.get(settings.csrf_cookie_name, "")
        csrf_header = request.headers.get(settings.csrf_header_name, "")
        if (
            not csrf_cookie
            or not csrf_header
            or not secrets.compare_digest(csrf_cookie, csrf_header)
            or not secrets.compare_digest(digest_token(csrf_cookie), session_record.csrf_token_hash)
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed.")

    # Avoid a SQLite write on every API call; refresh activity at most once per minute.
    if _as_utc(session_record.last_activity_at) <= now - timedelta(minutes=1):
        session_record.last_activity_at = now
        db.commit()

    return user


def clear_user_sessions(db: Session, user_id: str) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
    db.commit()
