"""Storage and queue figures shown on the dashboard."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import utcnow
from .models import OPEN_STATES, RUNNING_STATES, Download
from .settings_service import GB, AppSettings
from .storage import StorageManager


def _count(session: Session, *criteria) -> int:
    return session.query(func.count(Download.id)).filter(*criteria).scalar() or 0


def usage_summary(session: Session, settings: AppSettings, storage: StorageManager, user_id: int) -> dict:
    return {
        "storage_bytes": storage.usage("private", user_id),
        # 0 means "no limit".
        "storage_limit_bytes": int(settings.storage.max_storage_gb * GB),
        "max_concurrent": settings.downloads.max_global_concurrent,
        "active_downloads": _count(session, Download.state.in_([s.value for s in RUNNING_STATES])),
        "open_downloads": _count(session, Download.state.in_([s.value for s in OPEN_STATES])),
        "downloads_today": _count(session, Download.created_at >= utcnow() - timedelta(hours=24)),
    }
