"""Real-time event stream over WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .deps import ApiError, check_origin, get_ctx, local_user

router = APIRouter()

HEARTBEAT_SECONDS = 25


@router.websocket("/ws")
async def events(websocket: WebSocket) -> None:
    ctx = get_ctx(websocket)
    try:
        check_origin(websocket, ctx)
    except ApiError:
        await websocket.close(code=1008, reason="cross-site connection")
        return

    def profile() -> int:
        with ctx.db.session() as db:
            return local_user(db, ctx).id

    user_id = await asyncio.to_thread(profile)
    await websocket.accept()
    subscriber = ctx.bus.subscribe(user_id, True)

    async def sender() -> None:
        await websocket.send_text(
            json.dumps({"type": "hello", "data": {"server_time": time.time()}})
        )
        while True:
            try:
                message = await asyncio.wait_for(subscriber.queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                message = {"type": "ping", "data": {}}
            await websocket.send_text(json.dumps(message, separators=(",", ":")))

    async def receiver() -> None:
        while True:
            await websocket.receive_text()  # clients only send keep-alives

    tasks = [asyncio.create_task(coro()) for coro in (sender, receiver)]
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
            await websocket.close()
