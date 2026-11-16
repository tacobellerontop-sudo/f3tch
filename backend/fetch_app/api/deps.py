"""Shared FastAPI dependencies: context, DB session, authentication, CSRF."""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request, WebSocket
from sqlalchemy.orm import Session as DbSession
from starlette.requests import HTTPConnection

from ..context import AppContext
from ..db import utcnow
from ..models import Session, User
from ..security.crypto import hash_token

SESSION_COOKIE = "fetch_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


def get_ctx(conn: HTTPConnection) -> AppContext:
    return conn.app.state.ctx


Ctx = Annotated[AppContext, Depends(get_ctx)]


def get_db(ctx: Ctx) -> Iterator[DbSession]:
    session = ctx.db.new_session()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


Db = Annotated[DbSession, Depends(get_db)]


def _peer_trusted(conn: HTTPConnection, ctx: AppContext) -> bool:
    peer = conn.client.host if conn.client else ""
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for entry in ctx.settings.get().network.trusted_proxies:
        if address in ipaddress.ip_network(entry, strict=False):
            return True
    return False


def client_ip(conn: HTTPConnection, ctx: AppContext) -> str:
    """The client address; forwarded headers are honoured only from trusted proxies."""
    peer = conn.client.host if conn.client else ""
    if _peer_trusted(conn, ctx):
        forwarded = conn.headers.get("x-forwarded-for", "")
        candidate = forwarded.split(",")[-1].strip()
        if candidate:
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    return peer


def is_secure(conn: HTTPConnection, ctx: AppContext) -> bool:
    mode = ctx.boot.cookie_secure
    if mode in ("true", "always", "1"):
        return True
    if mode in ("false", "never", "0"):
        return False
    if conn.url.scheme in ("https", "wss"):
        return True
    return _peer_trusted(conn, ctx) and conn.headers.get("x-forwarded-proto", "") == "https"


def check_origin(conn: HTTPConnection, ctx: AppContext) -> None:
    """Reject cross-site requests. Browsers always send Origin on those."""
    origin = conn.headers.get("origin")
    if not origin or origin == "null":
        if origin == "null":
            raise ApiError(403, "bad_origin", "Cross-site requests are not allowed.")
        return
    host = conn.headers.get("host", "")
    forwarded_host = conn.headers.get("x-forwarded-host", "") if _peer_trusted(conn, ctx) else ""
    origin_host = urlsplit(origin).netloc
    network = ctx.settings.get().network
    allowed = {o.rstrip("/") for o in network.cors_origins}
    if network.public_url:
        allowed.add(network.public_url)
    if origin_host in (host, forwarded_host) or origin.rstrip("/") in allowed:
        return
    raise ApiError(403, "bad_origin", "Cross-site requests are not allowed.")


def load_session(conn: HTTPConnection, db: DbSession) -> tuple[Session, User] | None:
    token = conn.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = db.query(Session).filter(Session.token_hash == hash_token(token)).one_or_none()
    if row is None:
        return None
    now = utcnow()
    if row.expires_at <= now:
        db.delete(row)
        db.flush()
        return None
    user = row.user
    if user is None or not user.is_active:
        return None
    if now - row.last_seen_at > timedelta(seconds=60):
        row.last_seen_at = now
    return row, user


def current_session(request: Request, ctx: Ctx, db: Db) -> Session:
    if request.method not in SAFE_METHODS:
        check_origin(request, ctx)
    found = load_session(request, db)
    if found is None:
        raise ApiError(401, "not_authenticated", "Please sign in.")
    session, user = found
    if request.method not in SAFE_METHODS:
        supplied = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(supplied, session.csrf_token):
            raise ApiError(403, "csrf_failed", "Security check failed. Reload the page and try again.")
    request.state.user = user
    request.state.session = session
    return session


CurrentSession = Annotated[Session, Depends(current_session)]


def current_user(session: CurrentSession) -> User:
    return session.user


CurrentUser = Annotated[User, Depends(current_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise ApiError(403, "forbidden", "Administrator access is required.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def websocket_user(websocket: WebSocket, ctx: AppContext) -> tuple[Session, User] | None:
    try:
        check_origin(websocket, ctx)
    except ApiError:
        return None
    with ctx.db.session() as db:
        found = load_session(websocket, db)
        if found is None:
            return None
        session, user = found
        _ = (session.id, user.id, user.role)
        return session, user
