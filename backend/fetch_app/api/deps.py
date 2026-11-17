"""Shared FastAPI dependencies: context, DB session, the local profile, origin checks.

FETCH is a local application: it listens on this computer only and has no
accounts. Every request acts as the single built-in local profile. What keeps
other web pages away is the pair of checks in this module: the ``Host`` header
must name this computer (defeats DNS rebinding) and requests that change
something must come from FETCH's own pages (defeats cross-site requests).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DbSession
from starlette.requests import HTTPConnection

from ..context import AppContext
from ..models import Role, User

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_NAMES = {"localhost"}
LOCAL_USERNAME = "local"


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


def is_local_host(host: str) -> bool:
    """True for a host name or address (without port) that means "this computer"."""
    host = host.strip().lower().strip("[]")
    if host in LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _hostname(netloc: str) -> str:
    try:
        return urlsplit(f"//{netloc}").hostname or ""
    except ValueError:
        return ""


def is_local_request(scope: dict) -> bool:
    """Whether an ASGI connection comes from this computer and addresses it by a local name."""
    client = scope.get("client")
    if client and not is_local_host(str(client[0])):
        return False
    for name, value in scope.get("headers", ()):
        if name == b"host":
            return is_local_host(_hostname(value.decode("latin-1")))
    return False


def client_ip(conn: HTTPConnection, ctx: AppContext | None = None) -> str:
    return conn.client.host if conn.client else ""


def check_origin(conn: HTTPConnection, ctx: AppContext | None = None) -> None:
    """Reject cross-site requests. Browsers always send Origin on those."""
    origin = conn.headers.get("origin")
    if not origin:
        return
    if origin != "null" and urlsplit(origin).netloc == conn.headers.get("host", ""):
        return
    raise ApiError(403, "bad_origin", "Cross-site requests are not allowed.")


def local_user(db: DbSession, ctx: AppContext) -> User:
    """The built-in local profile that owns everything; created on first use.

    An installation that was set up by a version with accounts keeps its first
    administrator as the local profile, so downloads and files stay in place.
    """
    user = db.get(User, ctx.local_user_id) if ctx.local_user_id else None
    if user is None:
        user = (
            db.query(User)
            .order_by((User.role != Role.ADMIN.value), User.id)
            .first()
        )
        if user is None:
            user = User(username=LOCAL_USERNAME, display_name="", role=Role.ADMIN.value)
            db.add(user)
            db.flush()
        if not user.is_admin or not user.is_active:
            user.role, user.is_active = Role.ADMIN.value, True
            db.flush()
        ctx.local_user_id = user.id
    return user


def current_user(request: Request, ctx: Ctx, db: Db) -> User:
    if request.method not in SAFE_METHODS:
        check_origin(request, ctx)
    user = local_user(db, ctx)
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(current_user)]
# Kept as a separate name for the endpoints that change how FETCH itself runs.
AdminUser = CurrentUser
