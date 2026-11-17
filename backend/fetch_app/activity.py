"""Audit trail: persisted to ``activity_logs``, mirrored to the structured log."""

from __future__ import annotations

import logging
from typing import Any

from .db import Database
from .events import EventBus
from .logging_setup import redact
from .models import ActivityLog

log = logging.getLogger("fetch.audit")


class ActivityService:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self._db = db
        self._bus = bus

    def record(
        self,
        category: str,
        action: str,
        message: str,
        *,
        user_id: int | None = None,
        actor_id: int | None = None,
        level: str = "info",
        target: str = "",
        ip: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        detail = redact(detail or {})
        try:
            with self._db.session() as session:
                entry = ActivityLog(
                    category=category,
                    action=action,
                    message=message[:2000],
                    user_id=user_id,
                    actor_id=actor_id if actor_id != user_id else None,
                    level=level,
                    target=target[:255],
                    ip_address=ip[:64],
                    detail=detail,
                )
                session.add(entry)
                session.flush()
                payload = {
                    "id": entry.id,
                    "created_at": entry.created_at.isoformat() + "Z",
                    "category": category,
                    "action": action,
                    "level": level,
                    "message": entry.message,
                    "target": entry.target,
                    "user_id": user_id,
                }
        except Exception:  # the audit trail must never break the operation itself
            log.exception("Could not persist activity entry", extra={"action": action})
            return
        log.log(
            {"error": logging.ERROR, "warning": logging.WARNING}.get(level, logging.INFO),
            message,
            extra={
                "category": category,
                "action": action,
                "user_id": user_id,
                "actor_id": actor_id,
                "target": target,
                "client_ip": ip,
                "detail": detail,
            },
        )
        self._bus.publish(
            "activity", payload, user_id=user_id, audience="owner" if user_id else "admin"
        )
