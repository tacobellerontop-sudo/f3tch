"""Download submission, listing, control and queue ordering."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DbSession

from ..context import AppContext
from ..db import utcnow
from ..models import OPEN_STATES, Download, DownloadState, FileRecord, User
from ..providers.base import ErrorCode
from ..quotas import QuotaError, check_submission
from ..schemas import (
    BulkIn,
    CaptchaIn,
    DownloadPatchIn,
    InspectIn,
    ReorderIn,
    SubmitIn,
    serialize_download,
)
from ..security.urlguard import UnsafeUrlError
from ..storage import StorageError, clean_rel_path, sanitize_filename
from .deps import ApiError, Ctx, CurrentUser, Db, client_ip

router = APIRouter(prefix="/downloads", tags=["downloads"])

S = DownloadState
QUEUE_STATES = [S.PENDING.value, S.QUEUED.value, S.WAITING.value, S.PAUSED.value]
SORT_COLUMNS = {
    "created_at": Download.created_at,
    "filename": func.lower(Download.filename),
    "size": Download.total_bytes,
    "state": Download.state,
    "provider": Download.provider,
    "priority": Download.priority,
    "progress": Download.downloaded_bytes,
    "finished_at": Download.finished_at,
    "position": Download.position,
}


def _own(db: DbSession, user: User, job_id: int) -> Download:
    row = db.get(Download, job_id)
    # A foreign id is indistinguishable from a missing one.
    if row is None or (row.user_id != user.id and not user.is_admin):
        raise ApiError(404, "not_found", "Download not found.")
    return row


def _overlay(ctx: AppContext, row: Download) -> dict:
    """Serialize, replacing persisted counters with live ones for running jobs."""
    data = serialize_download(row)
    handle = ctx.manager.live(row.id)
    if handle is not None and row.state == S.DOWNLOADING.value:
        data["downloaded_bytes"] = handle.downloaded
        data["total_bytes"] = handle.total if handle.total is not None else data["total_bytes"]
        data["speed_bps"] = handle.speed
        data["avg_speed_bps"] = handle.average
        if handle.total and handle.speed > 0:
            data["eta_seconds"] = max(0.0, (handle.total - handle.downloaded) / handle.speed)
    # Tells the interface that this download is waiting for its owner.
    data["action_required"] = "captcha" if _captcha_pending(ctx, row) else ""
    return data


def _captcha_pending(ctx: AppContext, row: Download) -> bool:
    return (
        row.state == S.WAITING.value
        and row.error_code == ErrorCode.CAPTCHA_REQUIRED
        and ctx.manager.captcha(row.id) is not None
    )


def _validate_url(ctx: AppContext, url: str) -> str | None:
    try:
        ctx.guard.check_url(url)
        return None
    except UnsafeUrlError as exc:
        return str(exc)


async def _inspect_all(ctx: AppContext, urls: list[str]) -> list[dict]:
    gate = asyncio.Semaphore(4)

    async def one(url: str) -> dict:
        problem = _validate_url(ctx, url)
        if problem:
            return {
                "url": url, "provider": "", "provider_name": "", "supported": False,
                "filename": "", "size": None, "resumable": None,
                "restriction": ErrorCode.UNSAFE_URL, "message": problem,
                "premium_only": False, "auth_mode": "",
            }
        async with gate:
            info = await ctx.registry.inspect(url)
        data = info.as_dict()
        provider = ctx.registry.get(info.provider)
        data["provider_name"] = provider.name if provider else ""
        data["filename"] = sanitize_filename(info.filename, fallback="") if info.filename else ""
        return data

    return list(await asyncio.gather(*(one(url) for url in urls)))


@router.post("/inspect")
async def inspect(body: InspectIn, user: CurrentUser, ctx: Ctx) -> dict:
    """Detect the provider of each link and fetch its metadata (nothing is queued)."""
    return {"items": await _inspect_all(ctx, body.urls)}


@router.post("", status_code=201)
async def submit(body: SubmitIn, request: Request, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    settings = ctx.settings.get()
    destination = body.destination or settings.general.default_destination
    if destination == "shared":
        if not settings.storage.shared_enabled:
            raise ApiError(403, "shared_disabled", "The shared folder is disabled.")
        if not user.is_admin and not settings.storage.users_can_write_shared:
            raise ApiError(403, "forbidden", "Only administrators can save to the shared folder.")
    try:
        folder = clean_rel_path(body.folder)
        ctx.storage.resolve(destination, user.id, folder)
    except StorageError as exc:
        raise ApiError(422, "invalid_folder", str(exc)) from exc

    inspected = await _inspect_all(ctx, body.urls)
    accepted = [item for item in inspected if item["supported"]]
    rejected = [
        {"url": item["url"], "provider": item["provider"], "code": item["restriction"],
         "message": item["message"]}
        for item in inspected if not item["supported"]
    ]
    created: list[Download] = []
    if accepted:
        try:
            check_submission(
                db, user, settings, ctx.storage,
                new_jobs=len(accepted),
                known_sizes=[item["size"] for item in accepted if item["size"]],
                destination=destination,
            )
        except QuotaError as exc:
            raise ApiError(409, exc.code, exc.message) from exc
        priority = body.priority if body.priority is not None else settings.downloads.default_priority
        if priority > 2 and not user.is_admin:
            priority = 2  # "urgent" is reserved for administrators
        for item in accepted:
            row = Download(
                user_id=user.id,
                url=item["url"],
                provider=item["provider"],
                filename=item["filename"],
                total_bytes=item["size"],
                state=S.PENDING.value,
                priority=priority,
                destination=destination,
                folder=folder,
                max_attempts=settings.downloads.retry_count,
            )
            db.add(row)
            created.append(row)
        db.flush()
        payload = [serialize_download(row, owner=user.username) for row in created]
        db.commit()
        for data in payload:
            ctx.bus.publish("download.added", {"download": data}, user_id=user.id)
        ctx.activity.record(
            "download", "download.submitted",
            f"Submitted {len(created)} download" + ("s" if len(created) != 1 else ""),
            user_id=user.id, ip=client_ip(request, ctx),
            detail={"ids": [row.id for row in created],
                    "providers": sorted({row.provider for row in created})},
        )
        ctx.manager.wake()
    else:
        payload = []
    return {"created": payload, "rejected": rejected}


@router.get("")
def list_downloads(
    user: CurrentUser,
    ctx: Ctx,
    db: Db,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    state: str = Query("", max_length=200),
    provider: str = Query("", max_length=32),
    q: str = Query("", max_length=200),
    sort: str = Query("created_at"),
    order: Literal["asc", "desc"] = "desc",
    owner: str = Query("", max_length=16),
) -> dict:
    query = db.query(Download)
    if not user.is_admin or owner == "me":
        query = query.filter(Download.user_id == user.id)
    elif owner.isdigit():
        query = query.filter(Download.user_id == int(owner))
    states = [s for s in state.split(",") if s]
    if states:
        valid = {s.value for s in S}
        if any(s not in valid for s in states):
            raise ApiError(422, "invalid_state", "Unknown download state.")
        query = query.filter(Download.state.in_(states))
    if provider:
        query = query.filter(Download.provider == provider)
    if q.strip():
        text = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        needle = f"%{text}%"
        query = query.filter(
            or_(
                func.lower(Download.filename).like(needle, escape="\\"),
                func.lower(Download.url).like(needle, escape="\\"),
            )
        )
    total = query.count()
    if sort == "queue":
        query = query.order_by(Download.priority.desc(), Download.position, Download.id)
    else:
        column = SORT_COLUMNS.get(sort)
        if column is None:
            raise ApiError(422, "invalid_sort", "Unknown sort field.")
        query = query.order_by(column.asc() if order == "asc" else column.desc(), Download.id.desc())
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [_overlay(ctx, row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/stats")
def stats(user: CurrentUser, ctx: Ctx, db: Db, scope: Literal["me", "all"] = "me") -> dict:
    everyone = scope == "all" and user.is_admin
    query = db.query(Download.state, func.count(Download.id))
    if not everyone:
        query = query.filter(Download.user_id == user.id)
    counts = dict(query.group_by(Download.state).all())
    by_state = {s.value: counts.get(s.value, 0) for s in S}
    return {
        "by_state": by_state,
        "active": by_state["inspecting"] + by_state["downloading"] + by_state["verifying"],
        "queued": by_state["pending"] + by_state["queued"] + by_state["waiting"] + by_state["paused"],
        "completed": by_state["completed"],
        "failed": by_state["failed"],
        "speed_bps": ctx.manager.aggregate_speed(None if everyone else user.id),
    }


@router.get("/{job_id}")
def get_download(job_id: int, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    return _overlay(ctx, _own(db, user, job_id))


async def _control(ctx: AppContext, action: str, job_id: int) -> Download | None:
    manager = ctx.manager
    if action == "pause":
        return await manager.pause(job_id)
    if action == "resume":
        return await manager.resume(job_id)
    if action == "cancel":
        return await manager.cancel(job_id)
    return await manager.retry(job_id)


_ALLOWED_FROM = {
    "pause": {S.PENDING, S.QUEUED, S.WAITING, S.INSPECTING, S.DOWNLOADING},
    "resume": {S.PAUSED},
    "cancel": set(OPEN_STATES),
    "retry": {S.FAILED, S.CANCELLED},
}


_PAST = {"pause": "paused", "resume": "resumed", "cancel": "cancelled", "retry": "retried"}


@router.post("/bulk")
async def bulk(body: BulkIn, request: Request, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    rows = db.query(Download).filter(Download.id.in_(body.ids)).all()
    allowed = [r for r in rows if r.user_id == user.id or user.is_admin]
    states = {r.id: r.state for r in allowed}
    db.commit()
    done: list[int] = []
    skipped: list[int] = [i for i in body.ids if i not in states]
    for job_id, state in states.items():
        if body.action == "remove":
            if body.delete_files:
                _delete_output(ctx, job_id)
            if await ctx.manager.remove(job_id):
                done.append(job_id)
            else:
                skipped.append(job_id)
        elif S(state) in _ALLOWED_FROM[body.action]:
            await _control(ctx, body.action, job_id)
            done.append(job_id)
        else:
            skipped.append(job_id)
    if done:
        ctx.activity.record(
            "download", f"download.bulk_{body.action}", f"{body.action.title()} on {len(done)} downloads",
            user_id=user.id, ip=client_ip(request, ctx), detail={"ids": done},
        )
    return {"done": done, "skipped": skipped}


@router.post("/reorder")
def reorder(body: ReorderIn, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    """Reorder queued jobs. The given ids take over each other's positions.

    A regular user can only permute their own jobs, so they can never move
    ahead of somebody else's.
    """
    if len(set(body.ids)) != len(body.ids):
        raise ApiError(422, "invalid_order", "The list contains duplicates.")
    rows = db.query(Download).filter(Download.id.in_(body.ids)).all()
    by_id = {row.id: row for row in rows}
    for job_id in body.ids:
        row = by_id.get(job_id)
        if row is None or (row.user_id != user.id and not user.is_admin):
            raise ApiError(404, "not_found", "Download not found.")
        if row.state not in QUEUE_STATES:
            raise ApiError(409, "not_queued", "Only queued downloads can be reordered.")
    positions = sorted(row.position for row in rows)
    if len(set(positions)) != len(positions):  # legacy rows sharing a position
        top = db.query(func.max(Download.position)).scalar() or 0
        positions = list(range(top + 1, top + 1 + len(rows)))
    for job_id, position in zip(body.ids, positions, strict=True):
        by_id[job_id].position = position
    db.commit()
    ctx.bus.publish("queue.changed", {}, audience="all")
    ctx.manager.wake()
    return {"ok": True}


@router.patch("/{job_id}")
def update_download(job_id: int, body: DownloadPatchIn, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    row = _own(db, user, job_id)
    if body.priority > 2 and not user.is_admin:
        raise ApiError(403, "forbidden", "Only administrators can set urgent priority.")
    row.priority = body.priority
    row.updated_at = utcnow()
    db.flush()
    data = serialize_download(row)
    db.commit()
    ctx.bus.publish("download.updated", {"download": data}, user_id=row.user_id)
    ctx.bus.publish("queue.changed", {}, audience="all")
    ctx.manager.wake()
    return data


# -- CAPTCHA completed by the download's owner ----------------------------------
# FETCH relays the provider's challenge to the person and their answer back.
# It never solves one. These routes are declared before the generic
# ``/{job_id}/{action}`` route below so that they take precedence.


def _pending_captcha(ctx: AppContext, row: Download):
    challenge = ctx.manager.captcha(row.id) if _captcha_pending(ctx, row) else None
    if challenge is None:
        raise ApiError(404, "no_captcha", "This download is not waiting for a CAPTCHA.")
    return challenge


@router.get("/{job_id}/captcha")
def get_captcha(job_id: int, user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    row = _own(db, user, job_id)
    challenge = _pending_captcha(ctx, row)
    expires = ctx.manager.captcha_expires_at(job_id)
    return {
        "download_id": job_id,
        "provider": row.provider,
        "kind": challenge.kind,
        "prompt": challenge.prompt,
        "has_image": bool(challenge.image),
        "page_url": challenge.page_url,
        "accepts_download_url": challenge.accepts_download_url,
        "attempt": challenge.attempt,
        "expires_at": (
            datetime.fromtimestamp(expires, UTC).isoformat().replace("+00:00", "Z") if expires else None
        ),
    }


@router.get("/{job_id}/captcha/image")
def get_captcha_image(job_id: int, user: CurrentUser, ctx: Ctx, db: Db) -> Response:
    challenge = _pending_captcha(ctx, _own(db, user, job_id))
    if not challenge.image:
        raise ApiError(404, "no_captcha", "This CAPTCHA has no picture.")
    media_type = challenge.image_type if challenge.image_type in _IMAGE_TYPES else "application/octet-stream"
    return Response(
        challenge.image, media_type=media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; sandbox"},
    )


@router.post("/{job_id}/captcha")
async def submit_captcha(
    job_id: int, body: CaptchaIn, request: Request, user: CurrentUser, ctx: Ctx, db: Db
) -> dict:
    row = _own(db, user, job_id)
    challenge = _pending_captcha(ctx, row)
    answer, download_url = body.answer.strip(), body.download_url.strip()
    values: dict[str, str] = {}
    if download_url:
        if not challenge.accepts_download_url:
            raise ApiError(422, "invalid_captcha", "This provider does not accept a download address.")
        problem = _validate_url(ctx, download_url)
        provider = ctx.registry.get(row.provider)
        if problem is None and provider is not None:
            problem = provider.check_download_address(download_url)
        if problem:
            raise ApiError(422, "invalid_captcha", problem)
        values["download_url"] = download_url
    elif answer:
        if challenge.kind != "image":
            raise ApiError(
                422, "invalid_captcha",
                "This CAPTCHA can only be completed on the provider's own website.",
            )
        values["answer"] = answer
    else:
        raise ApiError(422, "invalid_captcha", "Enter the CAPTCHA answer.")
    owner_id = row.user_id
    db.commit()
    updated = await ctx.manager.submit_captcha(job_id, values)
    if updated is None:
        raise ApiError(409, "no_captcha", "This CAPTCHA is no longer valid. Retry the download.")
    ctx.activity.record(
        "download", "download.captcha_answered",
        f"CAPTCHA completed by the user: {updated.filename or updated.url}",
        user_id=owner_id, actor_id=user.id, ip=client_ip(request, ctx), target=str(job_id),
    )
    return _overlay(ctx, updated)


_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


@router.post("/{job_id}/{action}")
async def control(
    job_id: int, action: Literal["pause", "resume", "cancel", "retry"],
    request: Request, user: CurrentUser, ctx: Ctx, db: Db,
) -> dict:
    row = _own(db, user, job_id)
    if S(row.state) not in _ALLOWED_FROM[action]:
        raise ApiError(409, "invalid_state", f"A {row.state} download cannot be {_PAST[action]}.")
    owner_id = row.user_id
    db.commit()
    updated = await _control(ctx, action, job_id)
    if updated is None:
        raise ApiError(404, "not_found", "Download not found.")
    ctx.activity.record(
        "download", f"download.{action}", f"{action.title()}: {updated.filename or updated.url}",
        user_id=owner_id, actor_id=user.id, ip=client_ip(request, ctx), target=str(job_id),
    )
    return serialize_download(updated)


def _delete_output(ctx: AppContext, job_id: int) -> None:
    """Delete the file a completed download produced (if it still exists)."""
    with ctx.db.session() as session:
        row = session.get(Download, job_id)
        if row is None or row.state != S.COMPLETED.value or not row.final_path:
            return
        scope, owner, rel = row.destination, row.user_id, row.final_path
        session.query(FileRecord).filter_by(
            scope=scope, space=owner if scope == "private" else 0, rel_path=rel
        ).delete()
    try:
        ctx.storage.delete(scope, owner, rel)
    except StorageError:
        return
    ctx.bus.publish("storage.changed", {"scope": scope}, user_id=owner,
                    audience="all" if scope == "shared" else "owner")


@router.delete("/{job_id}")
async def remove(
    job_id: int, request: Request, user: CurrentUser, ctx: Ctx, db: Db,
    delete_file: bool = False,
) -> dict:
    row = _own(db, user, job_id)
    name, owner_id = row.filename or row.url, row.user_id
    db.commit()
    if delete_file:
        _delete_output(ctx, job_id)
    await ctx.manager.remove(job_id)
    ctx.activity.record(
        "download", "download.removed", f"Removed from history: {name}",
        user_id=owner_id, actor_id=user.id, ip=client_ip(request, ctx), target=str(job_id),
    )
    return {"ok": True}
