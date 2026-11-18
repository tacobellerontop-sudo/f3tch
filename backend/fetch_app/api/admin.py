"""Settings, activity, diagnostics, updates and local control."""

from __future__ import annotations

import asyncio
import platform
import sys

from fastapi import APIRouter, Query, Request
from sqlalchemy import func

from .. import __version__
from ..context import AppContext
from ..logging_setup import LOG_FILE_NAME, tail_log
from ..models import ActivityLog, Download, User
from ..settings_service import RESTART_REQUIRED
from .deps import ApiError, Ctx, CurrentUser, Db, client_ip, get_ctx

router = APIRouter(tags=["admin"])


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
def read_settings(user: CurrentUser, ctx: Ctx) -> dict:
    return _settings_payload(ctx)


@router.put("/settings")
def write_settings(body: dict, request: Request, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
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
            user_id=user.id, ip=client_ip(request, ctx), detail={"changed": changed},
        )
        ctx.bus.publish("settings.changed", {"changed": changed}, audience="all")
        ctx.manager.wake()
    return {**_settings_payload(ctx), "changed": changed}


# -- activity --------------------------------------------------------------------


def _activity_query(db, category: str, level: str, q: str):
    query = db.query(ActivityLog)
    if category:
        query = query.filter(ActivityLog.category == category)
    if level:
        query = query.filter(ActivityLog.level == level)
    if q.strip():
        text = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(func.lower(ActivityLog.message).like(f"%{text}%", escape="\\"))
    return query


@router.get("/activity")
def activity(
    user: CurrentUser,
    db: Db,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    category: str = Query("", max_length=24),
    level: str = Query("", max_length=8),
    q: str = Query("", max_length=100),
) -> dict:
    query = _activity_query(db, category, level, q)
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
            }
            for log in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/activity/{entry_id}")
def delete_activity_entry(entry_id: int, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    entry = db.get(ActivityLog, entry_id)
    if entry is None:
        raise ApiError(404, "not_found", "That activity entry no longer exists.")
    db.delete(entry)
    db.commit()
    ctx.bus.publish("activity.deleted", {"ids": [entry_id]}, audience="all")
    return {"deleted": 1}


@router.delete("/activity")
def clear_activity(
    user: CurrentUser,
    ctx: Ctx,
    db: Db,
    category: str = Query("", max_length=24),
    level: str = Query("", max_length=8),
    q: str = Query("", max_length=100),
) -> dict:
    """Delete every entry, or only the ones matching the same filters as the list."""
    deleted = _activity_query(db, category, level, q).delete(synchronize_session=False)
    db.commit()
    ctx.bus.publish("activity.deleted", {"cleared": deleted}, audience="all")
    return {"deleted": deleted}


# -- diagnostics -----------------------------------------------------------------


@router.get("/admin/health")
def health(user: CurrentUser, ctx: Ctx, db: Db) -> dict:
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
    user: CurrentUser, ctx: Ctx,
    limit: int = Query(200, ge=1, le=1000),
    level: str = Query("", max_length=10),
    q: str = Query("", max_length=100),
) -> dict:
    entries = tail_log(ctx.boot.log_dir / LOG_FILE_NAME, limit=limit, level=level or None, query=q)
    return {"items": entries, "file": str(ctx.boot.log_dir / LOG_FILE_NAME)}


@router.post("/admin/workers/restart")
async def restart_workers(request: Request, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    """Restart download workers. Running jobs keep their data and resume."""
    db.commit()
    interrupted = await ctx.manager.restart()
    ctx.activity.record(
        "admin", "workers.restarted", f"Download workers restarted ({interrupted} job(s) requeued)",
        user_id=user.id, ip=client_ip(request, ctx),
    )
    ctx.bus.publish("queue.changed", {}, audience="all")
    return {"ok": True, "requeued": interrupted}


@router.post("/admin/cleanup")
async def cleanup(request: Request, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    from ..maintenance import run_maintenance

    db.commit()
    result = await asyncio.to_thread(run_maintenance, ctx, True)
    ctx.activity.record(
        "admin", "storage.cleanup", "Storage cleanup run",
        user_id=user.id, ip=client_ip(request, ctx), detail=result,
    )
    return result


# -- updates ---------------------------------------------------------------------


@router.get("/admin/update")
async def update_status(user: CurrentUser, ctx: Ctx, db: Db, refresh: bool = False) -> dict:
    db.commit()
    return await ctx.updates.status(
        auto_check=ctx.settings.get().general.check_for_updates,
        refresh=refresh, managed=ctx.server is not None,
    )


@router.post("/admin/update/apply")
async def apply_update(request: Request, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    """Install the latest release: FETCH.exe replaces itself, a folder install runs update.ps1."""
    from ..updates import UpdateError

    db.commit()
    try:
        if ctx.updates.mode == "exe":
            await ctx.updates.apply_executable(ctx.server)
        else:
            await asyncio.to_thread(ctx.updates.start, managed=ctx.server is not None)
    except UpdateError as exc:
        raise ApiError(409, "update_unavailable", str(exc)) from exc
    ctx.activity.record(
        "admin", "update.started", "Update started from the dashboard",
        user_id=user.id, ip=client_ip(request, ctx),
    )
    return {"ok": True}


# -- local control (used by stop.ps1, `FETCH.exe stop` and a second start) ------


def _control(request: Request) -> AppContext:
    """Only programs that can read data/control.json may use these endpoints."""
    import hmac

    ctx = get_ctx(request)
    token = request.headers.get("x-fetch-control", "")
    if not ctx.control_token or not hmac.compare_digest(token, ctx.control_token):
        raise ApiError(404, "not_found", "Not found.")
    return ctx


@router.post("/control/show", include_in_schema=False)
def control_show(request: Request) -> dict:
    """Bring the window of the running FETCH to the front (FETCH was started twice)."""
    ctx = _control(request)
    if ctx.show_window is None:
        return {"ok": True, "window": False}
    ctx.show_window()
    return {"ok": True, "window": True}


@router.post("/control/shutdown", include_in_schema=False)
def control_shutdown(request: Request) -> dict:
    ctx = _control(request)
    if ctx.server is None:
        raise ApiError(409, "not_supported", "This server cannot be stopped through the API.")
    ctx.server.should_exit = True
    return {"ok": True}
