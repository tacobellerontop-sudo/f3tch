"""File manager: browse, search, download (streamed), show in the computer's
file manager, create folders, delete."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from ..models import FileRecord, User
from ..settings_service import GB
from ..schemas import FolderIn, RevealIn
from ..storage import StorageError, clean_rel_path
from .deps import ApiError, Ctx, CurrentUser, Db, client_ip

router = APIRouter(prefix="/files", tags=["files"])

Scope = Literal["private", "shared"]


def _space(user: User, scope: str) -> int:
    """Storage space key: the local profile's folder, or 0 for the shared folder
    (kept readable for files saved there by a version that had accounts)."""
    return 0 if scope == "shared" else user.id


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
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
) -> dict:
    space = _space(user, scope)
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

    items = []
    for entry in window:
        record = records.get(entry.path)
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
                "source_url": record.source_url if record else "",
            }
        )
    return {
        "scope": scope,
        "path": rel,
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/usage")
def usage(user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    settings = ctx.settings.get()
    return {
        "private": {
            "used_bytes": ctx.storage.usage("private", user.id),
            # 0 means "no limit".
            "limit_bytes": int(settings.storage.max_storage_gb * GB),
        },
        "shared": {"used_bytes": ctx.storage.usage("shared")},
        "disk": ctx.storage.disk(),
    }


@router.get("/download")
def download_file(
    request: Request,
    user: CurrentUser,
    ctx: Ctx,
    db: Db,
    path: str = Query(..., min_length=1, max_length=1024),
    scope: Scope = "private",
) -> FileResponse:
    space = _space(user, scope)
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


def open_in_file_manager(target: Path) -> None:
    """Show `target` in the computer's file manager: a file is selected in its
    folder, a folder is opened. Never runs or opens the file itself.
    Tests replace this function."""
    if sys.platform == "win32":
        if target.is_dir():
            subprocess.Popen(["explorer.exe", str(target)])  # noqa: S603, S607
        else:
            subprocess.Popen(["explorer.exe", "/select,", str(target)])  # noqa: S603, S607
    elif sys.platform == "darwin":
        args = ["open", str(target)] if target.is_dir() else ["open", "-R", str(target)]
        subprocess.Popen(args)  # noqa: S603, S607
    else:
        folder = target if target.is_dir() else target.parent
        subprocess.Popen(["xdg-open", str(folder)], start_new_session=True)  # noqa: S603, S607


@router.post("/reveal")
def reveal(body: RevealIn, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    space = _space(user, body.scope)
    try:
        target = ctx.storage.resolve(body.scope, space, body.path)
    except StorageError as exc:
        raise ApiError(404, "not_found", "File not found.") from exc
    db.commit()
    if not target.exists() or target.is_symlink():
        raise ApiError(404, "not_found", "File not found.")
    try:
        open_in_file_manager(target)
    except OSError as exc:
        raise ApiError(409, "reveal_failed", f"Could not open the file manager: {exc}") from exc
    return {"ok": True}


@router.post("/folder", status_code=201)
def create_folder(body: FolderIn, request: Request, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    space = _space(user, body.scope)
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
) -> dict:
    space = _space(user, scope)
    try:
        rel = clean_rel_path(path)
        target = ctx.storage.resolve(scope, space, rel)
    except StorageError as exc:
        raise ApiError(404, "not_found", "File not found.") from exc
    if not rel or not target.exists():
        raise ApiError(404, "not_found", "File not found.")
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
