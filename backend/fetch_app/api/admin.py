"""Administration: users, quotas, invitations, sessions, settings, diagnostics."""

from __future__ import annotations

import asyncio
import platform
import secrets
import sys
from datetime import timedelta

from fastapi import APIRouter, Query, Request
from sqlalchemy import func

from .. import __version__
from ..context import AppContext
from ..db import utcnow
from ..logging_setup import LOG_FILE_NAME, tail_log
from ..models import (
    OPEN_STATES,
    ActivityLog,
    Download,
    FileRecord,
    Invitation,
    Role,
    Session,
    User,
)
from ..quotas import get_quota
from ..schemas import (
    InvitationIn,
    InvitationOut,
    QuotaIn,
    QuotaOut,
    ResetPasswordIn,
    SessionOut,
    UserOut,
    UserUpdateIn,
)
from ..security.crypto import hash_token
from ..security.passwords import check_policy, generate_password, hash_password
from ..settings_service import RESTART_REQUIRED
from .deps import AdminUser, ApiError, Ctx, CurrentUser, Db, client_ip, get_ctx

router = APIRouter(tags=["admin"])


# -- users -----------------------------------------------------------------------


def _user(db, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "not_found", "User not found.")
    return user


def _other_active_admins(db, user: User) -> int:
    return (
        db.query(func.count(User.id))
        .filter(User.role == Role.ADMIN.value, User.is_active.is_(True), User.id != user.id)
        .scalar()
        or 0
    )


@router.get("/users")
def list_users(admin: AdminUser, ctx: Ctx, db: Db) -> list[dict]:
    settings = ctx.settings.get()
    open_counts = dict(
        db.query(Download.user_id, func.count(Download.id))
        .filter(Download.state.in_([s.value for s in OPEN_STATES]))
        .group_by(Download.user_id)
        .all()
    )
    total_counts = dict(
        db.query(Download.user_id, func.count(Download.id)).group_by(Download.user_id).all()
    )
    session_counts = dict(
        db.query(Session.user_id, func.count(Session.id))
        .filter(Session.expires_at > utcnow())
        .group_by(Session.user_id)
        .all()
    )
    result = []
    for user in db.query(User).order_by(User.id).all():
        data = UserOut.model_validate(user).model_dump(mode="json")
        data["quota"] = QuotaOut.model_validate(get_quota(db, user, settings)).model_dump()
        data["storage_bytes"] = ctx.storage.usage("private", user.id)
        data["open_downloads"] = open_counts.get(user.id, 0)
        data["total_downloads"] = total_counts.get(user.id, 0)
        data["active_sessions"] = session_counts.get(user.id, 0)
        result.append(data)
    return result


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: UserUpdateIn, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    user = _user(db, user_id)
    changes: list[str] = []
    demoting = body.role == Role.USER.value and user.is_admin
    disabling = body.is_active is False and user.is_active
    if (demoting or disabling) and user.is_admin and _other_active_admins(db, user) == 0:
        raise ApiError(409, "last_admin", "FETCH needs at least one active administrator.")
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or user.username
        changes.append("display name")
    if body.role is not None and body.role != user.role:
        user.role = body.role
        changes.append(f"role → {body.role}")
    if body.is_active is not None and body.is_active != user.is_active:
        user.is_active = body.is_active
        changes.append("enabled" if body.is_active else "disabled")
        if not body.is_active:
            db.query(Session).filter(Session.user_id == user.id).delete()
    data = UserOut.model_validate(user).model_dump(mode="json")
    db.commit()
    if disabling:
        ctx.bus.disconnect_user(user.id)
        await ctx.manager.stop_user_jobs(user.id)
    if changes:
        ctx.activity.record(
            "admin", "user.updated", f"User '{user.username}' updated: {', '.join(changes)}",
            user_id=admin.id, ip=client_ip(request, ctx), target=str(user.id),
        )
        ctx.manager.wake()
    ctx.bus.publish("users.changed", {}, audience="admin")
    return data


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int, request: Request, admin: AdminUser, ctx: Ctx, db: Db, delete_files: bool = False
) -> dict:
    user = _user(db, user_id)
    if user.id == admin.id:
        raise ApiError(409, "self_delete", "You cannot delete your own account.")
    if user.is_admin and user.is_active and _other_active_admins(db, user) == 0:
        raise ApiError(409, "last_admin", "FETCH needs at least one active administrator.")
    username = user.username
    job_ids = [row.id for row in db.query(Download.id).filter(Download.user_id == user.id)]
    db.commit()
    ctx.bus.disconnect_user(user_id)
    await ctx.manager.stop_user_jobs(user_id, "cancel")
    with ctx.db.session() as session:
        session.query(FileRecord).filter(
            FileRecord.scope == "private", FileRecord.space == user_id
        ).delete()
        session.query(Download).filter(Download.user_id == user_id).delete()
        target = session.get(User, user_id)
        if target is not None:
            session.delete(target)
    for job_id in job_ids:
        ctx.storage.remove_job_temp(job_id)
    removed_files = False
    if delete_files:
        import shutil

        root = ctx.storage.space_root("private", user_id)
        await asyncio.to_thread(shutil.rmtree, root, True)
        ctx.storage.invalidate_usage("private", user_id)
        removed_files = True
    ctx.activity.record(
        "admin", "user.deleted",
        f"User '{username}' deleted" + (" with their files" if removed_files else " (files kept on disk)"),
        user_id=admin.id, ip=client_ip(request, ctx), target=str(user_id),
    )
    ctx.bus.publish("users.changed", {}, audience="admin")
    ctx.bus.publish("queue.changed", {}, audience="all")
    return {"ok": True, "files_deleted": removed_files}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: ResetPasswordIn, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    user = _user(db, user_id)
    security = ctx.settings.get().security
    generated = not body.new_password
    password = body.new_password or generate_password(max(16, security.password_min_length))
    problem = check_policy(password, min_length=security.password_min_length,
                           require_mixed=security.password_require_mixed)
    if problem:
        raise ApiError(422, "weak_password", problem)
    user.password_hash = hash_password(password)
    user.must_change_password = True
    db.query(Session).filter(Session.user_id == user.id, Session.user_id != admin.id).delete()
    username = user.username
    db.commit()
    if user_id != admin.id:
        ctx.bus.disconnect_user(user_id)
    ctx.activity.record(
        "admin", "user.password_reset", f"Password reset for '{username}'",
        user_id=admin.id, ip=client_ip(request, ctx), target=str(user_id),
    )
    # A generated password is shown exactly once, to the administrator.
    return {"ok": True, "temporary_password": password if generated else None}


@router.get("/users/{user_id}/quota")
def read_quota(user_id: int, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    return QuotaOut.model_validate(get_quota(db, _user(db, user_id), ctx.settings.get())).model_dump()


@router.put("/users/{user_id}/quota")
def write_quota(user_id: int, body: QuotaIn, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    user = _user(db, user_id)
    quota = get_quota(db, user, ctx.settings.get())
    for key, value in body.model_dump().items():
        setattr(quota, key, value)
    data = QuotaOut.model_validate(quota).model_dump()
    username = user.username
    db.commit()
    ctx.activity.record(
        "admin", "user.quota_updated", f"Quota updated for '{username}'",
        user_id=admin.id, ip=client_ip(request, ctx), target=str(user_id), detail=data,
    )
    ctx.bus.publish("users.changed", {}, audience="admin")
    ctx.bus.publish("quota.changed", {}, user_id=user_id)
    ctx.manager.wake()
    return data


# -- invitations -----------------------------------------------------------------


def _invitation_out(row: Invitation) -> dict:
    out = InvitationOut.model_validate(row)
    if row.used_at:
        out.status = "used"
    elif row.revoked:
        out.status = "revoked"
    elif row.expires_at <= utcnow():
        out.status = "expired"
    else:
        out.status = "active"
    return out.model_dump(mode="json")


@router.get("/invitations")
def list_invitations(admin: AdminUser, db: Db) -> list[dict]:
    rows = db.query(Invitation).order_by(Invitation.id.desc()).limit(200).all()
    return [_invitation_out(row) for row in rows]


@router.post("/invitations", status_code=201)
def create_invitation(body: InvitationIn, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    settings = ctx.settings.get()
    token = secrets.token_urlsafe(24)
    hours = body.expires_hours or settings.security.invitation_expiry_hours
    row = Invitation(
        token_hash=hash_token(token), role=body.role, note=body.note.strip(),
        created_by_id=admin.id, expires_at=utcnow() + timedelta(hours=hours),
    )
    db.add(row)
    db.flush()
    data = _invitation_out(row)
    db.commit()
    base = settings.network.public_url or str(request.base_url).rstrip("/")
    ctx.activity.record(
        "admin", "invitation.created", f"Invitation created ({body.role})",
        user_id=admin.id, ip=client_ip(request, ctx), target=str(row.id),
    )
    # The token exists only in this response; the database keeps its hash.
    return {**data, "token": token, "path": f"/invite/{token}", "url": f"{base}/invite/{token}"}


@router.delete("/invitations/{invitation_id}")
def revoke_invitation(invitation_id: int, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    row = db.get(Invitation, invitation_id)
    if row is None:
        raise ApiError(404, "not_found", "Invitation not found.")
    if row.used_at is None and not row.revoked and row.expires_at > utcnow():
        row.revoked = True
    else:
        db.delete(row)
    db.commit()
    ctx.activity.record(
        "admin", "invitation.revoked", "Invitation revoked",
        user_id=admin.id, ip=client_ip(request, ctx), target=str(invitation_id),
    )
    return {"ok": True}


# -- sessions --------------------------------------------------------------------


@router.get("/admin/sessions")
def all_sessions(admin: AdminUser, request: Request, db: Db) -> list[dict]:
    current = request.state.session.id
    rows = (
        db.query(Session, User.username)
        .join(User, User.id == Session.user_id)
        .filter(Session.expires_at > utcnow())
        .order_by(Session.last_seen_at.desc())
        .all()
    )
    result = []
    for row, username in rows:
        out = SessionOut.model_validate(row)
        out.username, out.current = username, row.id == current
        result.append(out.model_dump(mode="json"))
    return result


@router.delete("/admin/sessions/{session_id}")
def revoke_session(session_id: int, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    row = db.get(Session, session_id)
    if row is None:
        raise ApiError(404, "not_found", "Session not found.")
    if row.id == request.state.session.id:
        raise ApiError(400, "current_session", "Use Sign out to end the current session.")
    user_id = row.user_id
    db.delete(row)
    db.commit()
    ctx.bus.disconnect_user(user_id, session_id)
    ctx.activity.record(
        "admin", "session.revoked", "Session revoked",
        user_id=admin.id, ip=client_ip(request, ctx), target=str(user_id),
    )
    return {"ok": True}


# -- settings --------------------------------------------------------------------


def _settings_payload(ctx: AppContext) -> dict:
    host, port = ctx.settings.effective_bind()
    return {
        "settings": ctx.settings.get().model_dump(),
        "effective": {
            "bind_address": host,
            "port": port,
            "storage_root": ctx.settings.effective_storage_root(),
            "active_storage_root": str(ctx.storage.root),
            "config_file": str(ctx.boot.config_file),
        },
        "restart_required": sorted(ctx.pending_restart),
    }


@router.get("/settings")
def read_settings(admin: AdminUser, ctx: Ctx) -> dict:
    return _settings_payload(ctx)


@router.put("/settings")
def write_settings(body: dict, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    db.commit()
    try:
        _, changed = ctx.settings.update(body)
    except ValueError as exc:
        message = str(exc)
        errors = getattr(exc, "errors", None)
        if callable(errors):
            first = errors()[0]
            location = ".".join(str(part) for part in first.get("loc", ()))
            message = f"{location}: {first.get('msg', 'invalid value')}"
        raise ApiError(422, "invalid_settings", message) from exc
    if changed:
        ctx.pending_restart.update(name for name in changed if name in RESTART_REQUIRED)
        ctx.activity.record(
            "admin", "settings.updated", f"Settings changed: {', '.join(changed)}",
            user_id=admin.id, ip=client_ip(request, ctx), detail={"changed": changed},
        )
        ctx.bus.publish("settings.changed", {"changed": changed}, audience="all")
        ctx.manager.wake()
    return {**_settings_payload(ctx), "changed": changed}


# -- activity --------------------------------------------------------------------


@router.get("/activity")
def activity(
    user: CurrentUser,
    db: Db,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    category: str = Query("", max_length=24),
    level: str = Query("", max_length=8),
    scope: str = Query("me", max_length=8),
    q: str = Query("", max_length=100),
) -> dict:
    query = db.query(ActivityLog, User.username).outerjoin(User, User.id == ActivityLog.user_id)
    if not (user.is_admin and scope == "all"):
        query = query.filter(ActivityLog.user_id == user.id)
        if not user.is_admin:
            # Administrative actions on an account stay in the admin audit trail.
            query = query.filter(ActivityLog.category.in_(["auth", "download", "file", "provider"]))
    if category:
        query = query.filter(ActivityLog.category == category)
    if level:
        query = query.filter(ActivityLog.level == level)
    if q.strip():
        text = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(func.lower(ActivityLog.message).like(f"%{text}%", escape="\\"))
    total = query.count()
    rows = (
        query.order_by(ActivityLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    )
    return {
        "items": [
            {
                "id": log.id,
                "created_at": log.created_at.isoformat() + "Z",
                "category": log.category,
                "action": log.action,
                "level": log.level,
                "message": log.message,
                "target": log.target,
                "username": username or "",
                "ip_address": log.ip_address if user.is_admin else "",
            }
            for log, username in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# -- diagnostics -----------------------------------------------------------------


@router.get("/admin/health")
def health(admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    import time

    disk = ctx.storage.disk()
    db_ok = True
    try:
        db.query(func.count(User.id)).scalar()
    except Exception:
        db_ok = False
    try:
        db_size = ctx.boot.database_path.stat().st_size
    except OSError:
        db_size = 0
    host, port = ctx.settings.effective_bind()
    counts = dict(db.query(Download.state, func.count(Download.id)).group_by(Download.state).all())
    checks = [
        {"name": "Database", "ok": db_ok, "detail": f"SQLite, {db_size} bytes"},
        {"name": "Download manager", "ok": ctx.manager.running,
         "detail": f"{ctx.manager.active_count} active job(s)"},
        {"name": "Storage", "ok": ctx.storage.root.is_dir() and disk["free"] > 512 * 1024 * 1024,
         "detail": str(ctx.storage.root)},
        {"name": "aria2 engine", "ok": ctx.aria2.available, "optional": True,
         "detail": f"version {ctx.aria2.version}" if ctx.aria2.available
         else (ctx.aria2.error or "Disabled in settings; the native engine is used.")},
        {"name": "Frontend build", "ok": (ctx.boot.frontend_dist / "index.html").is_file(),
         "detail": str(ctx.boot.frontend_dist)},
    ]
    return {
        "status": "ok" if all(c["ok"] or c.get("optional") for c in checks) else "degraded",
        "checks": checks,
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "uptime_seconds": int(time.time() - ctx.started_at),
        "listening": {"bind_address": host, "port": port},
        "manager": ctx.manager.diagnostics(),
        "downloads": counts,
        "disk": disk,
        "storage_used_bytes": ctx.storage.total_usage(),
        "realtime_clients": ctx.bus.subscriber_count,
        "guard_proxy_denied": ctx.proxy.denied,
        "restart_required": sorted(ctx.pending_restart),
    }


@router.get("/admin/logs")
def logs(
    admin: AdminUser, ctx: Ctx,
    limit: int = Query(200, ge=1, le=1000),
    level: str = Query("", max_length=10),
    q: str = Query("", max_length=100),
) -> dict:
    entries = tail_log(ctx.boot.log_dir / LOG_FILE_NAME, limit=limit, level=level or None, query=q)
    return {"items": entries, "file": str(ctx.boot.log_dir / LOG_FILE_NAME)}


@router.post("/admin/workers/restart")
async def restart_workers(request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    """Restart download workers. Running jobs keep their data and resume."""
    db.commit()
    interrupted = await ctx.manager.restart()
    ctx.activity.record(
        "admin", "workers.restarted", f"Download workers restarted ({interrupted} job(s) requeued)",
        user_id=admin.id, ip=client_ip(request, ctx),
    )
    ctx.bus.publish("queue.changed", {}, audience="all")
    return {"ok": True, "requeued": interrupted}


@router.post("/admin/cleanup")
async def cleanup(request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    from ..maintenance import run_maintenance

    db.commit()
    result = await asyncio.to_thread(run_maintenance, ctx, True)
    ctx.activity.record(
        "admin", "storage.cleanup", "Storage cleanup run",
        user_id=admin.id, ip=client_ip(request, ctx), detail=result,
    )
    return result


# -- local control (used by stop.ps1 / `python -m fetch_app stop`) --------------


@router.post("/control/shutdown", include_in_schema=False)
def control_shutdown(request: Request) -> dict:
    import hmac

    ctx = get_ctx(request)
    peer = request.client.host if request.client else ""
    token = request.headers.get("x-fetch-control", "")
    # Local only: loopback, or this machine's own address when FETCH is bound to
    # one specific interface (a remote peer can never have that source address).
    local = {"127.0.0.1", "::1"}
    bound = ctx.settings.effective_bind()[0]
    if bound not in ("0.0.0.0", "::", ""):
        local.add(bound)
    if peer not in local or not ctx.control_token or not hmac.compare_digest(
        token, ctx.control_token
    ):
        raise ApiError(404, "not_found", "Not found.")
    if ctx.server is None:
        raise ApiError(409, "not_supported", "This server cannot be stopped through the API.")
    ctx.server.should_exit = True
    return {"ok": True}
