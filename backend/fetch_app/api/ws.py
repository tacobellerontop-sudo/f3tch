"""Real-time event stream over WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import utcnow
from ..models import Session
from .deps import get_ctx, websocket_user

router = APIRouter()

SESSION_RECHECK_SECONDS = 60
HEARTBEAT_SECONDS = 25


@router.websocket("/ws")
async def events(websocket: WebSocket) -> None:
    ctx = get_ctx(websocket)
    found = await asyncio.to_thread(websocket_user, websocket, ctx)
    if found is None:
        # 4401: the client should stop reconnecting and send the user to sign in.
        await websocket.accept()
        await websocket.close(code=4401, reason="not authenticated")
        return
    session, user = found
    session_id, user_id, is_admin = session.id, user.id, user.is_admin
    await websocket.accept()
    subscriber = ctx.bus.subscribe(user_id, is_admin, session_id)

    def session_alive() -> bool:
        with ctx.db.session() as db:
            row = db.get(Session, session_id)
            return bool(
                row and row.expires_at > utcnow() and row.user and row.user.is_active
                and row.user.is_admin == is_admin
            )

    async def sender() -> None:
        await websocket.send_text(
            json.dumps({"type": "hello", "data": {"user_id": user_id, "server_time": time.time()}})
        )
        last_check = time.monotonic()
        while True:
            try:
                message = await asyncio.wait_for(subscriber.queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                message = {"type": "ping", "data": {}}
            if time.monotonic() - last_check > SESSION_RECHECK_SECONDS:
                last_check = time.monotonic()
                if not await asyncio.to_thread(session_alive):
                    return
            await websocket.send_text(json.dumps(message, separators=(",", ":")))

    async def receiver() -> None:
        while True:
            await websocket.receive_text()  # clients only send keep-alives

    async def closer() -> None:
        await subscriber.closed.wait()

    tasks = [asyncio.create_task(coro()) for coro in (sender, receiver, closer)]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        ctx.bus.unsubscribe(subscriber)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
                await task
        with contextlib.suppress(Exception):
            await websocket.close(code=4401 if subscriber.closed.is_set() else 1000)
