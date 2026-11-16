"""Real-time event bus with per-user authorization.

Events are published from anywhere (including worker threads) and fanned out
to WebSocket subscribers. Every event carries the id of the user it belongs
to; a regular user only ever receives their own events and events addressed
to everyone, administrators receive everything.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger("fetch.events")

Audience = Literal["owner", "all", "admin"]
QUEUE_LIMIT = 512


@dataclass(eq=False)
class Subscriber:
    user_id: int
    is_admin: bool
    session_id: int = 0
    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_LIMIT)
    )
    closed: asyncio.Event = field(default_factory=asyncio.Event)

    def may_receive(self, user_id: int | None, audience: Audience) -> bool:
        if audience == "all":
            return True
        if self.is_admin:
            return True
        if audience == "admin":
            return False
        return user_id is not None and user_id == self.user_id


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_thread = threading.get_ident()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self, user_id: int, is_admin: bool, session_id: int = 0) -> Subscriber:
        sub = Subscriber(user_id=user_id, is_admin=is_admin, session_id=session_id)
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subscribers.discard(sub)

    def disconnect_user(self, user_id: int, session_id: int | None = None) -> None:
        """Close live connections of a user (optionally one session only)."""

        def _close() -> None:
            for sub in list(self._subscribers):
                if sub.user_id == user_id and session_id in (None, sub.session_id):
                    sub.closed.set()

        self._call(_close)

    def publish(
        self,
        event: str,
        data: dict[str, Any] | None = None,
        *,
        user_id: int | None = None,
        audience: Audience = "owner",
    ) -> None:
        """Thread-safe publish."""
        message = {"type": event, "data": data or {}}
        self._call(lambda: self._dispatch(message, user_id, audience))

    def _call(self, fn) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        if threading.get_ident() == self._loop_thread:
            fn()
        else:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(fn)

    def _dispatch(self, message: dict[str, Any], user_id: int | None, audience: Audience) -> None:
        for sub in list(self._subscribers):
            if not sub.may_receive(user_id, audience):
                continue
            if sub.queue.full():
                # A slow client: drop its oldest update rather than block everyone.
                with contextlib.suppress(asyncio.QueueEmpty):
                    sub.queue.get_nowait()
            sub.queue.put_nowait(message)
