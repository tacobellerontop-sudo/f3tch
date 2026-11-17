"""WebSocket event stream and per-user event authorization."""

import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from fetch_app.events import EventBus, Subscriber

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


def test_subscriber_authorization_matrix():
    alice, admin = Subscriber(user_id=2, is_admin=False), Subscriber(user_id=1, is_admin=True)
    assert alice.may_receive(2, "owner") and not alice.may_receive(3, "owner")
    assert not alice.may_receive(None, "owner") and not alice.may_receive(2, "admin")
    assert alice.may_receive(None, "all")
    assert admin.may_receive(3, "owner") and admin.may_receive(None, "admin")


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


def test_unauthenticated_websocket_is_closed(api):
    with api.client.websocket_connect("/api/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4401


def test_cross_origin_websocket_is_refused(admin):
    with admin.client.websocket_connect("/api/ws", headers={"Origin": "https://evil.example"}) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4401


def test_download_lifecycle_events_are_pushed(admin, fileserver):
    data = fileserver.add("stream.bin", payload(900_000))
    with admin.client.websocket_connect("/api/ws") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello" and hello["data"]["user_id"] == admin.user["id"]
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


def test_users_never_receive_each_others_events(admin, user, fileserver):
    fileserver.add("secret-admin-file.bin", payload(300_000))
    fileserver.add("alice.bin", payload(1000))
    with user.client.websocket_connect("/api/ws") as alice_ws, admin.client.websocket_connect("/api/ws") as admin_ws:
        json.loads(alice_ws.receive_text()), json.loads(admin_ws.receive_text())
        admin_job = admin.submit(fileserver.url("slow", "secret-admin-file.bin"))["created"][0]["id"]
        admin.wait_state(admin_job, "completed")
        alice_job = user.submit(fileserver.url("file", "alice.bin"))["created"][0]["id"]
        alice_events = collect(alice_ws, lambda e: e["type"] == "download.completed")
        admin_events = collect(
            admin_ws, lambda e: e["type"] == "download.completed" and e["data"]["download"]["id"] == alice_job
        )
    # Alice saw her own job and nothing that identifies the admin's download.
    assert "secret-admin-file" not in json.dumps(alice_events)
    for event in alice_events:
        data = event["data"]
        assert data.get("id") != admin_job
        assert data.get("download", {}).get("id") != admin_job
        assert data.get("user_id") in (None, user.user["id"])
    # Administrators observe everybody's jobs.
    seen = {e["data"]["download"]["id"] for e in admin_events if "download" in e["data"]}
    assert seen == {admin_job, alice_job}


def test_pause_event_and_removal_event(admin, fileserver):
    fileserver.add("p.bin", payload(600_000))
    with admin.client.websocket_connect("/api/ws") as ws:
        json.loads(ws.receive_text())
        job_id = admin.submit(fileserver.url("slow", "p.bin"))["created"][0]["id"]
        collect(ws, lambda e: e["type"] == "download.progress")
        admin.post(f"/downloads/{job_id}/pause")
        paused = collect(ws, lambda e: e["type"] == "download.paused")[-1]
        assert paused["data"]["download"]["state"] == "paused"
        admin.delete(f"/downloads/{job_id}")
        removed = collect(ws, lambda e: e["type"] == "download.removed")[-1]
        assert removed["data"] == {"id": job_id}


def test_socket_closes_when_session_is_revoked(admin, user):
    with user.client.websocket_connect("/api/ws") as ws:
        json.loads(ws.receive_text())
        session_id = next(s["id"] for s in admin.get("/admin/sessions").json() if s["username"] == "alice")
        assert admin.delete(f"/admin/sessions/{session_id}").status_code == 200
        with pytest.raises(WebSocketDisconnect):
            for _ in range(50):
                ws.receive_text()
    assert user.get("/auth/me").status_code == 401
