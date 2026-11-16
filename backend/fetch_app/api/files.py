"""File manager: browse, search, download (streamed), create folders, delete."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DbSession

from ..context import AppContext
from ..models import FileRecord, User
from ..quotas import get_quota
from ..schemas import FolderIn
from ..storage import StorageError, clean_rel_path
from .deps import ApiError, Ctx, CurrentUser, Db, client_ip

router = APIRouter(prefix="/files", tags=["files"])

Scope = Literal["private", "shared"]


def _space(ctx: AppContext, db: DbSession, user: User, scope: str, owner_id: int | None) -> int:
    """Resolve whose space is addressed and check the caller may read it."""
    if scope == "shared":
        if not ctx.settings.get().storage.shared_enabled and not user.is_admin:
            raise ApiError(403, "shared_disabled", "The shared folder is disabled.")
        return 0
    if owner_id is None or owner_id == user.id:
        return user.id
    if not user.is_admin:
        # Never reveal whether another user's path exists.
        raise ApiError(404, "not_found", "File not found.")
    if db.get(User, owner_id) is None:
        raise ApiError(404, "not_found", "User not found.")
    return owner_id


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


@router.get("")
def list_files(
    user: CurrentUser,
    ctx: Ctx,
    db: Db,
    scope: Scope = "private",
    path: str = Query("", max_length=1024),
    q: str = Query("", max_length=200),
    type: str = Query("", max_length=16),
    sort: Literal["name", "size", "modified"] = "name",
    order: Literal["asc", "desc"] = "asc",
    owner_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
) -> dict:
    space = _space(ctx, db, user, scope, owner_id)
    try:
        rel = clean_rel_path(path)
        entries = (
            ctx.storage.search(scope, space, rel, q.strip())
            if q.strip()
            else ctx.storage.list_dir(scope, space, rel)
        )
    except StorageError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    if type:
        entries = [e for e in entries if e.type == type or (e.is_dir and not q.strip())]
    keys = {
        "name": lambda e: e.name.lower(),
        "size": lambda e: e.size,
        "modified": lambda e: e.modified,
    }
    entries.sort(key=keys[sort], reverse=order == "desc")
    entries.sort(key=lambda e: not e.is_dir)  # folders first, stable
    total = len(entries)
    window = entries[(page - 1) * page_size : page * page_size]

    records = {
        r.rel_path: r
        for r in db.query(FileRecord)
        .filter(
            FileRecord.scope == scope,
            FileRecord.space == space,
            FileRecord.rel_path.in_([e.path for e in window if not e.is_dir]),
        )
        .all()
    } if window else {}
    owners: dict[int, str] = {}
    if scope == "shared":
        ids = {r.owner_id for r in records.values() if r.owner_id}
        if ids:
            owners = dict(db.query(User.id, User.username).filter(User.id.in_(ids)).all())

    items = []
    for entry in window:
        record = records.get(entry.path)
        can_see_source = record is not None and (user.is_admin or record.owner_id == user.id)
        items.append(
            {
                "name": entry.name,
                "path": entry.path,
                "is_dir": entry.is_dir,
                "size": entry.size,
                "type": entry.type,
                "modified_at": _iso(entry.modified),
                "downloaded_at": record.created_at.isoformat() + "Z" if record else None,
                "provider": record.provider if record else "",
                "source_url": record.source_url if can_see_source else "",
                "owner": owners.get(record.owner_id, "") if record and record.owner_id else "",
                "can_delete": _may_delete(ctx, user, scope, record, entry.is_dir),
            }
        )
    return {
        "scope": scope,
        "path": rel,
        "owner_id": space if scope == "private" else None,
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "can_write": _may_write(ctx, user, scope),
    }


def _may_write(ctx: AppContext, user: User, scope: str) -> bool:
    if scope == "private" or user.is_admin:
        return True
    storage = ctx.settings.get().storage
    return storage.shared_enabled and storage.users_can_write_shared


def _may_delete(ctx: AppContext, user: User, scope: str, record: FileRecord | None, is_dir: bool) -> bool:
    if scope == "private" or user.is_admin:
        return True
    # In the shared space a regular user may only delete files they added.
    return (
        _may_write(ctx, user, scope) and not is_dir
        and record is not None and record.owner_id == user.id
    )


@router.get("/usage")
def usage(user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    settings = ctx.settings.get()
    quota = get_quota(db, user, settings)
    data = {
        "private": {
            "used_bytes": ctx.storage.usage("private", user.id),
            "limit_bytes": quota.max_storage_bytes,
        },
        "shared": {
            "enabled": settings.storage.shared_enabled,
            "used_bytes": ctx.storage.usage("shared") if settings.storage.shared_enabled else 0,
        },
    }
    if user.is_admin:
        data["disk"] = ctx.storage.disk()
        data["users"] = [
            {"user_id": uid, "username": name, "used_bytes": ctx.storage.usage("private", uid)}
            for uid, name in db.query(User.id, User.username).order_by(User.username).all()
        ]
    return data


@router.get("/download")
def download_file(
    request: Request,
    user: CurrentUser,
    ctx: Ctx,
    db: Db,
    path: str = Query(..., min_length=1, max_length=1024),
    scope: Scope = "private",
    owner_id: int | None = None,
) -> FileResponse:
    space = _space(ctx, db, user, scope, owner_id)
    try:
        target = ctx.storage.resolve(scope, space, path)
    except StorageError as exc:
        raise ApiError(404, "not_found", "File not found.") from exc
    if not target.is_file() or target.is_symlink():
        raise ApiError(404, "not_found", "File not found.")
    db.commit()
    if "range" not in request.headers:
        ctx.activity.record(
            "file", "file.downloaded", f"Retrieved {target.name}",
            user_id=user.id, ip=client_ip(request, ctx), target=clean_rel_path(path),
            detail={"scope": scope, "space": space},
        )
    # Always an attachment with a neutral type: stored files are never rendered
    # by the browser in FETCH's origin, and never executed by the server.
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/octet-stream",
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.post("/folder", status_code=201)
def create_folder(body: FolderIn, request: Request, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    space = _space(ctx, db, user, body.scope, body.owner_id)
    if not _may_write(ctx, user, body.scope):
        raise ApiError(403, "forbidden", "You cannot create folders here.")
    try:
        created = ctx.storage.make_folder(body.scope, space, body.path, body.name)
    except StorageError as exc:
        raise ApiError(422, "invalid_folder", str(exc)) from exc
    db.commit()
    ctx.activity.record(
        "file", "folder.created", f"Created folder {created.name}",
        user_id=user.id, ip=client_ip(request, ctx), detail={"scope": body.scope, "space": space},
    )
    return {"ok": True, "name": created.name}


@router.delete("")
def delete_file(
    request: Request,
    user: CurrentUser,
    ctx: Ctx,
    db: Db,
    path: str = Query(..., min_length=1, max_length=1024),
    scope: Scope = "private",
    owner_id: int | None = None,
) -> dict:
    space = _space(ctx, db, user, scope, owner_id)
    try:
        rel = clean_rel_path(path)
        target = ctx.storage.resolve(scope, space, rel)
    except StorageError as exc:
        raise ApiError(404, "not_found", "File not found.") from exc
    if not rel or not target.exists():
        raise ApiError(404, "not_found", "File not found.")
    record = (
        db.query(FileRecord).filter_by(scope=scope, space=space, rel_path=rel).one_or_none()
    )
    if not _may_delete(ctx, user, scope, record, target.is_dir()):
        raise ApiError(403, "forbidden", "You can only delete files you added.")
    escaped = rel.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    db.query(FileRecord).filter(
        FileRecord.scope == scope,
        FileRecord.space == space,
        (FileRecord.rel_path == rel) | FileRecord.rel_path.like(f"{escaped}/%", escape="\\"),
    ).delete(synchronize_session=False)
    db.commit()
    try:
        freed = ctx.storage.delete(scope, space, rel)
    except (StorageError, OSError) as exc:
        raise ApiError(409, "delete_failed", f"Could not delete: {exc}") from exc
    ctx.bus.publish("storage.changed", {"scope": scope}, user_id=space or None,
                    audience="all" if scope == "shared" else "owner")
    ctx.activity.record(
        "file", "file.deleted", f"Deleted {target.name}",
        user_id=space or user.id, actor_id=user.id, ip=client_ip(request, ctx), target=rel,
        detail={"scope": scope, "bytes": freed},
    )
    return {"ok": True, "freed_bytes": freed}
