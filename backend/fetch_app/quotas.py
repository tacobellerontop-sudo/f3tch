"""Per-user quota lookup and enforcement helpers (always server-side)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import utcnow
from .models import OPEN_STATES, RUNNING_STATES, Download, User, UserQuota
from .settings_service import GB, AppSettings
from .storage import StorageManager


class QuotaError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def quota_from_defaults(settings: AppSettings, user_id: int, *, admin: bool) -> UserQuota:
    if admin:  # administrators start unlimited
        return UserQuota(
            user_id=user_id, max_concurrent=0, max_storage_bytes=0, max_file_bytes=0,
            daily_downloads=0, bandwidth_bps=0, max_queued=0,
        )
    d = settings.quota_defaults
    return UserQuota(
        user_id=user_id,
        max_concurrent=d.max_concurrent,
        max_storage_bytes=int(d.max_storage_gb * GB),
        max_file_bytes=int(d.max_file_gb * GB),
        daily_downloads=d.daily_downloads,
        bandwidth_bps=d.bandwidth_kbps * 1024,
        max_queued=d.max_queued,
    )


def get_quota(session: Session, user: User, settings: AppSettings) -> UserQuota:
    quota = session.get(UserQuota, user.id)
    if quota is None:
        quota = quota_from_defaults(settings, user.id, admin=user.is_admin)
        session.add(quota)
        session.flush()
    return quota


def concurrency_limit(quota: UserQuota, settings: AppSettings) -> int:
    limit = settings.downloads.max_per_user_concurrent
    return min(limit, quota.max_concurrent) if quota.max_concurrent > 0 else limit


def downloads_today(session: Session, user_id: int) -> int:
    since = utcnow() - timedelta(hours=24)
    return (
        session.query(func.count(Download.id))
        .filter(Download.user_id == user_id, Download.created_at >= since)
        .scalar()
        or 0
    )


def open_jobs(session: Session, user_id: int) -> int:
    return (
        session.query(func.count(Download.id))
        .filter(Download.user_id == user_id, Download.state.in_([s.value for s in OPEN_STATES]))
        .scalar()
        or 0
    )


def reserved_bytes(session: Session, user_id: int, exclude_id: int | None = None) -> int:
    """Bytes still to arrive for the user's open downloads with a known size."""
    rows = (
        session.query(Download.id, Download.total_bytes)
        .filter(
            Download.user_id == user_id,
            Download.destination == "private",
            Download.state.in_([s.value for s in OPEN_STATES]),
            Download.total_bytes.isnot(None),
        )
        .all()
    )
    return sum(total for job_id, total in rows if job_id != exclude_id)


def usage_summary(
    session: Session, user: User, settings: AppSettings, storage: StorageManager
) -> dict:
    quota = get_quota(session, user, settings)
    active = (
        session.query(func.count(Download.id))
        .filter(Download.user_id == user.id, Download.state.in_([s.value for s in RUNNING_STATES]))
        .scalar()
        or 0
    )
    return {
        "limits": {
            "max_concurrent": concurrency_limit(quota, settings),
            "max_storage_bytes": quota.max_storage_bytes,
            "max_file_bytes": quota.max_file_bytes,
            "daily_downloads": quota.daily_downloads,
            "bandwidth_bps": quota.bandwidth_bps,
            "max_queued": quota.max_queued,
        },
        "usage": {
            "storage_bytes": storage.usage("private", user.id),
            "active_downloads": active,
            "open_downloads": open_jobs(session, user.id),
            "downloads_today": downloads_today(session, user.id),
        },
    }


def check_submission(
    session: Session,
    user: User,
    settings: AppSettings,
    storage: StorageManager,
    *,
    new_jobs: int,
    known_sizes: list[int],
    destination: str,
) -> None:
    """Raise :class:`QuotaError` if ``user`` may not add ``new_jobs`` downloads."""
    quota = get_quota(session, user, settings)
    if quota.max_queued > 0:
        current = open_jobs(session, user.id)
        if current + new_jobs > quota.max_queued:
            raise QuotaError(
                "queue_limit",
                f"Your queue limit is {quota.max_queued} downloads; you have {current} open.",
            )
    if quota.daily_downloads > 0:
        today = downloads_today(session, user.id)
        if today + new_jobs > quota.daily_downloads:
            raise QuotaError(
                "daily_limit",
                f"Your daily limit is {quota.daily_downloads} downloads; "
                f"{max(0, quota.daily_downloads - today)} remaining in the last 24 hours.",
            )
    if quota.max_file_bytes > 0 and any(size > quota.max_file_bytes for size in known_sizes):
        raise QuotaError(
            "file_too_large",
            "One of the files is larger than your maximum allowed file size.",
        )
    if destination == "private" and quota.max_storage_bytes > 0:
        used = storage.usage("private", user.id) + reserved_bytes(session, user.id)
        if used + sum(known_sizes) > quota.max_storage_bytes:
            raise QuotaError(
                "storage_quota_exceeded", "These downloads would exceed your storage allocation."
            )
