"""WebSocket event stream."""

import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from fetch_app.events import EventBus

from .conftest import payload


def collect(ws, until, limit=400):
    """Read events until ``until(event)`` is true; returns everything read."""
    events = []
    for _ in range(limit):
        event = json.loads(ws.receive_text())
        events.append(event)
        if until(event):
            return events
    raise AssertionError(f"event not seen; got {[e['type'] for e in events]}")


def test_bus_drops_oldest_for_slow_clients():
    import asyncio

    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        sub = bus.subscribe(1, False)
        for i in range(600):
            bus.publish("tick", {"i": i}, user_id=1)
        assert sub.queue.qsize() == 512
        assert sub.queue.get_nowait()["data"]["i"] == 88

    asyncio.run(scenario())


def test_cross_origin_websocket_is_refused(admin):
    with pytest.raises(WebSocketDisconnect) as closed:
        with admin.client.websocket_connect("ws://127.0.0.1:8787/api/ws", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_text()
    assert closed.value.code == 1008


def test_websocket_under_a_foreign_host_name_is_refused(admin):
    """A page elsewhere that resolves its own name to 127.0.0.1 (DNS rebinding)."""
    with pytest.raises(WebSocketDisconnect) as closed:
        with admin.client.websocket_connect("ws://127.0.0.1:8787/api/ws", headers={"Host": "evil.example:8787"}) as ws:
            ws.receive_text()
    assert closed.value.code == 1008


def test_download_lifecycle_events_are_pushed(admin, fileserver):
    data = fileserver.add("stream.bin", payload(900_000))
    with admin.client.websocket_connect("ws://127.0.0.1:8787/api/ws") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello" and hello["data"]["server_time"] > 0
        job_id = admin.submit(fileserver.url("slow", "stream.bin"))["created"][0]["id"]
        events = collect(
            ws, lambda e: e["type"] == "activity" and e["data"]["action"] == "download.completed"
        )
    types = [e["type"] for e in events]
    for expected in ("download.added", "download.started", "download.progress", "download.completed",
                     "queue.changed", "storage.changed", "activity"):
        assert expected in types, f"{expected} missing from {sorted(set(types))}"
    assert types.index("download.added") < types.index("download.started") < types.index("download.completed")
    progress = [e["data"] for e in events if e["type"] == "download.progress"]
    assert len(progress) >= 2, "several real progress updates"
    assert all(p["id"] == job_id and p["total_bytes"] == len(data) for p in progress)
    values = [p["downloaded_bytes"] for p in progress]
    assert values == sorted(values) and 0 < values[-1] <= len(data)
    assert any(p["speed_bps"] > 0 for p in progress)
    # Updates are throttled rather than sent for every chunk.
    assert len(progress) < 20
    final = next(e for e in events if e["type"] == "download.completed")["data"]["download"]
    assert final["state"] == "completed" and final["downloaded_bytes"] == len(data)


def test_pause_event_and_removal_event(admin, fileserver):
    fileserver.add("p.bin", payload(600_000))
    with admin.client.websocket_connect("ws://127.0.0.1:8787/api/ws") as ws:
        json.loads(ws.receive_text())
        job_id = admin.submit(fileserver.url("slow", "p.bin"))["created"][0]["id"]
        collect(ws, lambda e: e["type"] == "download.progress")
        admin.post(f"/downloads/{job_id}/pause")
        paused = collect(ws, lambda e: e["type"] == "download.paused")[-1]
        assert paused["data"]["download"]["state"] == "paused"
        admin.delete(f"/downloads/{job_id}")
        removed = collect(ws, lambda e: e["type"] == "download.removed")[-1]
        assert removed["data"] == {"id": job_id}
