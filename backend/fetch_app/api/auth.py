"""Authentication, first-run setup, invitations (public side) and profile."""

from __future__ import annotations

import hmac
import secrets
from datetime import timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from .. import __version__
from ..context import AppContext
from ..db import utcnow
from ..models import Invitation, Role, Session, User
from ..quotas import get_quota, usage_summary
from ..schemas import (
    LoginIn,
    PasswordChangeIn,
    ProfileIn,
    RegisterIn,
    SessionOut,
    SetupIn,
    UserOut,
)
from ..security.crypto import hash_token
from ..security.passwords import check_policy, hash_password, needs_rehash, verify_password
from .deps import (
    SESSION_COOKIE,
    ApiError,
    Ctx,
    CurrentSession,
    CurrentUser,
    Db,
    check_origin,
    client_ip,
    is_secure,
)

router = APIRouter(tags=["auth"])


def _has_users(db: DbSession) -> bool:
    return db.query(User.id).first() is not None


def _policy(ctx: AppContext, password: str) -> None:
    security = ctx.settings.get().security
    problem = check_policy(
        password, min_length=security.password_min_length,
        require_mixed=security.password_require_mixed,
    )
    if problem:
        raise ApiError(422, "weak_password", problem)


def start_session(request: Request, response: Response, ctx: AppContext, db: DbSession, user: User) -> Session:
    token = secrets.token_urlsafe(32)
    hours = ctx.settings.get().security.session_hours
    session = Session(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_token=secrets.token_urlsafe(24),
        expires_at=utcnow() + timedelta(hours=hours),
        ip_address=client_ip(request, ctx)[:64],
        user_agent=request.headers.get("user-agent", "")[:256],
    )
    db.add(session)
    user.last_login_at = utcnow()
    db.flush()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=hours * 3600,
        httponly=True,
        secure=is_secure(request, ctx),
        samesite="lax",
        path="/",
    )
    return session


def me_payload(ctx: AppContext, db: DbSession, user: User, session: Session) -> dict:
    return {
        "user": UserOut.model_validate(user).model_dump(mode="json"),
        "csrf_token": session.csrf_token,
        "quota": usage_summary(db, user, ctx.settings.get(), ctx.storage),
        "session_expires_at": session.expires_at.isoformat() + "Z",
    }


@router.get("/bootstrap")
def bootstrap(ctx: Ctx, db: Db) -> dict:
    """Public facts the login screen needs. Contains nothing sensitive."""
    settings = ctx.settings.get()
    return {
        "app_name": settings.general.app_name,
        "version": __version__,
        "setup_required": not _has_users(db),
        "invitations_required": settings.security.invitations_required,
        "default_theme": settings.general.theme,
        "language": settings.general.language,
        "password_min_length": settings.security.password_min_length,
        "password_require_mixed": settings.security.password_require_mixed,
    }


@router.post("/setup")
def setup(body: SetupIn, request: Request, response: Response, ctx: Ctx, db: Db) -> dict:
    """Create the first administrator. Only works while no account exists."""
    check_origin(request, ctx)
    ip = client_ip(request, ctx)
    keys = [f"setup:{ip}"]
    wait = ctx.login_limiter.retry_after(keys)
    if wait:
        raise ApiError(429, "rate_limited", f"Too many attempts. Try again in {wait} seconds.",
                       headers={"Retry-After": str(wait)})
    if _has_users(db):
        raise ApiError(409, "already_set_up", "FETCH has already been set up.")
    expected = ctx.boot.setup_token
    if not expected or not hmac.compare_digest(body.setup_token.strip(), expected):
        ctx.login_limiter.record_failure(keys, max_attempts=5, window_seconds=900, lockout_seconds=900)
        ctx.activity.record("auth", "setup.denied", "Setup attempt with a wrong setup token",
                            level="warning", ip=ip)
        raise ApiError(403, "bad_setup_token", "The setup token is not correct.")
    _policy(ctx, body.password)
    user = User(
        username=body.username,
        display_name=body.display_name.strip() or body.username,
        password_hash=hash_password(body.password),
        role=Role.ADMIN.value,
    )
    db.add(user)
    db.flush()
    get_quota(db, user, ctx.settings.get())
    session = start_session(request, response, ctx, db, user)
    db.commit()
    ctx.activity.record("auth", "setup.completed", f"Administrator '{user.username}' created",
                        user_id=user.id, ip=ip)
    return me_payload(ctx, db, user, session)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, ctx: Ctx, db: Db) -> dict:
    check_origin(request, ctx)
    security = ctx.settings.get().security
    ip = client_ip(request, ctx)
    username = body.username.strip()
    keys = [f"ip:{ip}", f"user:{username.lower()}"]
    wait = ctx.login_limiter.retry_after(keys)
    if wait:
        raise ApiError(429, "rate_limited", f"Too many sign-in attempts. Try again in {wait} seconds.",
                       headers={"Retry-After": str(wait)})
    user = db.query(User).filter(func.lower(User.username) == username.lower()).one_or_none() if username else None
    valid = verify_password(user.password_hash if user else None, body.password)
    if not valid or user is None or not user.is_active:
        ctx.login_limiter.record_failure(
            keys,
            max_attempts=security.login_max_attempts,
            window_seconds=security.login_window_minutes * 60,
            lockout_seconds=security.login_lockout_minutes * 60,
        )
        ctx.activity.record(
            "auth", "login.failed", f"Failed sign-in for '{username[:64]}'",
            user_id=user.id if user else None, level="warning", ip=ip,
        )
        # One message for every cause, so accounts cannot be enumerated.
        raise ApiError(401, "invalid_credentials", "Incorrect username or password.")
    ctx.login_limiter.record_success(keys)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    session = start_session(request, response, ctx, db, user)
    db.commit()
    ctx.activity.record("auth", "login", "Signed in", user_id=user.id, ip=ip)
    return me_payload(ctx, db, user, session)


@router.post("/auth/logout")
def logout(request: Request, response: Response, session: CurrentSession, ctx: Ctx, db: Db) -> dict:
    user_id, session_id = session.user_id, session.id
    db.delete(session)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    ctx.bus.disconnect_user(user_id, session_id)
    ctx.activity.record("auth", "logout", "Signed out", user_id=user_id, ip=client_ip(request, ctx))
    return {"ok": True}


@router.get("/auth/me")
def me(user: CurrentUser, session: CurrentSession, ctx: Ctx, db: Db) -> dict:
    return me_payload(ctx, db, user, session)


@router.patch("/auth/me")
def update_profile(body: ProfileIn, user: CurrentUser, session: CurrentSession, ctx: Ctx, db: Db) -> dict:
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or user.username
    if body.theme is not None:
        user.theme = body.theme
    db.flush()
    return me_payload(ctx, db, user, session)


@router.post("/auth/password")
def change_password(
    body: PasswordChangeIn, request: Request, user: CurrentUser, session: CurrentSession, ctx: Ctx, db: Db
) -> dict:
    ip = client_ip(request, ctx)
    keys = [f"pw:{user.id}"]
    wait = ctx.login_limiter.retry_after(keys)
    if wait:
        raise ApiError(429, "rate_limited", f"Too many attempts. Try again in {wait} seconds.")
    if not verify_password(user.password_hash, body.current_password):
        ctx.login_limiter.record_failure(keys, max_attempts=5, window_seconds=900, lockout_seconds=900)
        raise ApiError(403, "invalid_credentials", "Your current password is not correct.")
    _policy(ctx, body.new_password)
    if body.new_password == body.current_password:
        raise ApiError(422, "weak_password", "The new password must differ from the current one.")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # Every other session of this account is signed out.
    others = db.query(Session).filter(Session.user_id == user.id, Session.id != session.id).all()
    for other in others:
        ctx.bus.disconnect_user(user.id, other.id)
        db.delete(other)
    db.commit()
    ctx.activity.record("auth", "password.changed", "Password changed", user_id=user.id, ip=ip)
    return {"ok": True, "sessions_revoked": len(others)}


@router.get("/auth/sessions")
def my_sessions(user: CurrentUser, session: CurrentSession, db: Db) -> list[dict]:
    rows = (
        db.query(Session)
        .filter(Session.user_id == user.id, Session.expires_at > utcnow())
        .order_by(Session.last_seen_at.desc())
        .all()
    )
    result = []
    for row in rows:
        out = SessionOut.model_validate(row)
        out.username = user.username
        out.current = row.id == session.id
        result.append(out.model_dump(mode="json"))
    return result


@router.delete("/auth/sessions/{session_id}")
def revoke_my_session(session_id: int, user: CurrentUser, session: CurrentSession, ctx: Ctx, db: Db) -> dict:
    row = db.get(Session, session_id)
    if row is None or row.user_id != user.id:
        raise ApiError(404, "not_found", "Session not found.")
    if row.id == session.id:
        raise ApiError(400, "current_session", "Use Sign out to end the current session.")
    db.delete(row)
    db.commit()
    ctx.bus.disconnect_user(user.id, session_id)
    return {"ok": True}


def find_invitation(db: DbSession, token: str) -> Invitation | None:
    if not token:
        return None
    row = db.query(Invitation).filter(Invitation.token_hash == hash_token(token)).one_or_none()
    if row is None or row.revoked or row.used_at is not None or row.expires_at <= utcnow():
        return None
    return row


@router.get("/auth/invitation/{token}")
def check_invitation(token: str, request: Request, ctx: Ctx, db: Db) -> dict:
    keys = [f"invite:{client_ip(request, ctx)}"]
    wait = ctx.login_limiter.retry_after(keys)
    if wait:
        raise ApiError(429, "rate_limited", f"Too many attempts. Try again in {wait} seconds.")
    row = find_invitation(db, token)
    if row is None:
        ctx.login_limiter.record_failure(keys, max_attempts=10, window_seconds=600, lockout_seconds=600)
        raise ApiError(404, "invalid_invitation", "This invitation is invalid, expired or already used.")
    return {"valid": True, "role": row.role, "expires_at": row.expires_at.isoformat() + "Z"}


@router.post("/auth/register")
def register(body: RegisterIn, request: Request, response: Response, ctx: Ctx, db: Db) -> dict:
    check_origin(request, ctx)
    settings = ctx.settings.get()
    ip = client_ip(request, ctx)
    keys = [f"register:{ip}"]
    wait = ctx.login_limiter.retry_after(keys)
    if wait:
        raise ApiError(429, "rate_limited", f"Too many attempts. Try again in {wait} seconds.")
    if not _has_users(db):
        raise ApiError(409, "setup_required", "FETCH has not been set up yet.")
    invitation = find_invitation(db, body.token)
    if invitation is None and (settings.security.invitations_required or body.token):
        ctx.login_limiter.record_failure(keys, max_attempts=10, window_seconds=600, lockout_seconds=600)
        raise ApiError(403, "invalid_invitation", "This invitation is invalid, expired or already used.")
    if not settings.security.invitations_required and invitation is None:
        # Open registration is rate limited per address.
        ctx.login_limiter.record_failure(keys, max_attempts=5, window_seconds=3600, lockout_seconds=3600)
    _policy(ctx, body.password)
    if db.query(User.id).filter(func.lower(User.username) == body.username.lower()).first():
        raise ApiError(409, "username_taken", "That username is already taken.")
    user = User(
        username=body.username,
        display_name=body.display_name.strip() or body.username,
        password_hash=hash_password(body.password),
        role=invitation.role if invitation else Role.USER.value,
    )
    db.add(user)
    db.flush()
    if invitation is not None:
        invitation.used_at = utcnow()
        invitation.used_by_id = user.id
    get_quota(db, user, settings)
    session = start_session(request, response, ctx, db, user)
    db.commit()
    ctx.activity.record(
        "auth", "register", f"Account '{user.username}' created"
        + (" from an invitation" if invitation else ""),
        user_id=user.id, ip=ip,
    )
    ctx.bus.publish("users.changed", {}, audience="admin")
    return me_payload(ctx, db, user, session)
