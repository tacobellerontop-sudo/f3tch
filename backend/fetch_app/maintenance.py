"""Periodic housekeeping: expired sessions, temporary files, retention."""

from __future__ import annotations

import logging
from datetime import timedelta

from .context import AppContext
from .db import utcnow
from .models import (
    FINISHED_STATES,
    OPEN_STATES,
    Download,
    FileRecord,
    Invitation,
    Session,
)
from .storage import StorageError

log = logging.getLogger("fetch.maintenance")


def run_maintenance(ctx: AppContext, force_temp: bool = False) -> dict:
    settings = ctx.settings.get().storage
    now = utcnow()
    result = {"sessions": 0, "temp_folders": 0, "files": 0, "history": 0, "invitations": 0}
    with ctx.db.session() as session:
        result["sessions"] = session.query(Session).filter(Session.expires_at <= now).delete()
        result["invitations"] = (
            session.query(Invitation)
            .filter(Invitation.expires_at <= now - timedelta(days=30))
            .delete()
        )
        keep = {
            row.id
            for row in session.query(Download.id).filter(
                Download.state.in_([s.value for s in OPEN_STATES])
            )
        }
        if settings.history_retention_days > 0:
            cutoff = now - timedelta(days=settings.history_retention_days)
            result["history"] = (
                session.query(Download)
                .filter(
                    Download.state.in_([s.value for s in FINISHED_STATES]),
                    Download.finished_at <= cutoff,
                )
                .delete(synchronize_session=False)
            )
        expired: list[tuple[int, str, int, str]] = []
        if settings.auto_delete_days > 0:
            cutoff = now - timedelta(days=settings.auto_delete_days)
            for record in session.query(FileRecord).filter(FileRecord.created_at <= cutoff).all():
                expired.append((record.id, record.scope, record.space, record.rel_path))
                session.delete(record)
    try:
        # Failed and cancelled jobs keep no partial data; orphaned folders go
        # after the configured age (immediately when an admin asks for it).
        result["temp_folders"] = ctx.storage.cleanup_temp(
            keep, 0 if force_temp else settings.temp_cleanup_hours
        )
    except OSError:
        log.exception("Temporary file cleanup failed")
    for _id, scope, space, rel_path in expired:
        try:
            ctx.storage.delete(scope, space, rel_path)
            result["files"] += 1
        except (StorageError, OSError):
            continue
    if result["files"]:
        ctx.bus.publish("storage.changed", {}, audience="all")
    if any(result.values()):
        log.info("Maintenance pass finished", extra={"result": result})
    return result
