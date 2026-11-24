from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    clear_user_sessions,
    digest_token,
    new_opaque_token,
    require_session,
    utc_now,
    verify_browser_origin,
)
from app.db.session import get_db
from app.models.auth import AuthLoginAttempt, AuthSession, AuthUser
from app.services.hpc.access import disconnect_hpc_access

router = APIRouter()
password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)

# A fixed dummy hash reduces the obvious timing difference between unknown-user and
# wrong-password login attempts. It is never associated with an actual account.
_DUMMY_PASSWORD_HASH = password_hasher.hash("SPI dummy password hash; never a real password")


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


def _login_key(username: str, client_host: str) -> str:
    material = f"{client_host}\0{username.strip().casefold()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _set_session_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    max_age = settings.session_absolute_hours * 60 * 60
    common = {
        "max_age": max_age,
        "path": "/",
        "secure": settings.cookie_secure,
        "samesite": "strict",
    }
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        httponly=True,
        **common,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        **common,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    """Public bootstrap status; does not reveal the configured username."""
    configured = db.scalar(select(func.count()).select_from(AuthUser)) > 0
    return {"admin_configured": configured}


@router.post("/login")
def login(payload: LoginPayload, request: Request, response: Response, db: Session = Depends(get_db)):
    verify_browser_origin(request)
    username = payload.username.strip().casefold()
    client_host = request.client.host if request.client else "unknown"
    attempt_key = _login_key(username, client_host)
    now = utc_now()
    window_start = now - timedelta(minutes=15)

    # Keep only recent failed-attempt records and cap at five per username/client pair.
    db.execute(delete(AuthLoginAttempt).where(AuthLoginAttempt.attempted_at < window_start))
    recent_attempts = db.scalar(
        select(func.count()).select_from(AuthLoginAttempt).where(
            AuthLoginAttempt.attempt_key == attempt_key,
            AuthLoginAttempt.attempted_at >= window_start,
        )
    ) or 0
    if recent_attempts >= 5:
        db.commit()
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in 15 minutes.")

    user = db.scalar(select(AuthUser).where(AuthUser.username == username))
    stored_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    valid_password = False
    try:
        valid_password = password_hasher.verify(stored_hash, payload.password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        valid_password = False

    if user is None or not user.is_active or not valid_password:
        db.add(AuthLoginAttempt(attempt_key=attempt_key, attempted_at=now))
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    # Rehash using current parameters if Argon2 settings have changed in a future release.
    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = password_hasher.hash(payload.password)

    db.execute(delete(AuthLoginAttempt).where(AuthLoginAttempt.attempt_key == attempt_key))
    # This is a single-user workstation installation: only one active browser session.
    clear_user_sessions(db, user.id)

    session_token = new_opaque_token()
    csrf_token = new_opaque_token()
    absolute_expiry = now + timedelta(hours=settings.session_absolute_hours)
    db.add(AuthSession(
        token_hash=digest_token(session_token),
        csrf_token_hash=digest_token(csrf_token),
        user_id=user.id,
        last_activity_at=now,
        expires_at=absolute_expiry,
    ))
    db.commit()

    _set_session_cookies(response, session_token, csrf_token)
    return {"id": user.id, "username": user.username, "expires_at": absolute_expiry.isoformat()}


@router.get("/me")
def current_user(user: AuthUser = Depends(require_session)):
    return {"id": user.id, "username": user.username}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    user: AuthUser = Depends(require_session),
    db: Session = Depends(get_db),
):
    # Signing out also releases the workstation's SPI HPC identity lease.
    # Do not prevent logout if Windows Agent/Task Scheduler is unavailable.
    try:
        disconnect_hpc_access(db)
    except Exception:
        pass

    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        session_record = db.get(AuthSession, digest_token(raw_token))
        if session_record is not None and session_record.user_id == user.id:
            db.delete(session_record)
            db.commit()
    _clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
