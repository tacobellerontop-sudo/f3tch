"""Download schedule (only download during chosen hours)."""

import time
from datetime import datetime, timedelta

from fetch_app.engine.schedule import evaluate

from .conftest import payload

EVERY_DAY = list(range(7))


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone()


def test_off_means_always_open():
    state = evaluate(False, "01:00", "07:00", EVERY_DAY, at("2026-11-18T12:00:00"))
    assert state.open and state.next_change is None


def test_overnight_window():
    inside = evaluate(True, "23:00", "06:00", EVERY_DAY, at("2026-11-18T02:30:00"))
    assert inside.open and inside.next_change == at("2026-11-18T06:00:00")
    outside = evaluate(True, "23:00", "06:00", EVERY_DAY, at("2026-11-18T12:00:00"))
    assert not outside.open and outside.next_change == at("2026-11-18T23:00:00")


def test_only_chosen_days():
    # 2026-11-21 is a Saturday (5). Weekends only:
    friday = evaluate(True, "09:00", "17:00", [5, 6], at("2026-11-20T10:00:00"))
    assert not friday.open and friday.next_change == at("2026-11-21T09:00:00")
    saturday_night_window = evaluate(True, "22:00", "02:00", [5], at("2026-11-22T01:00:00"))
    assert saturday_night_window.open, "an overnight window belongs to the day it starts"


def test_whole_day_every_day_never_closes_in_view():
    state = evaluate(True, "00:00", "00:00", EVERY_DAY, at("2026-11-18T12:00:00"))
    assert state.open and state.next_change >= at("2026-11-25T00:00:00")


def closed_now() -> dict:
    """A schedule whose window is a minute that is not now."""
    start = (datetime.now() + timedelta(hours=3)).strftime("%H:%M")
    stop = (datetime.now() + timedelta(hours=3, minutes=1)).strftime("%H:%M")
    return {"schedule_enabled": True, "schedule_start": start, "schedule_stop": stop, "schedule_days": EVERY_DAY}


def test_queue_waits_outside_the_schedule(admin, fileserver):
    admin.put("/settings", json={"downloads": closed_now()}).raise_for_status()
    stats = admin.get("/downloads/stats").json()
    assert stats["schedule"]["enabled"] and not stats["schedule"]["open"]
    fileserver.add("later.bin", payload(50_000))
    job_id = admin.submit(fileserver.url("file", "later.bin"))["created"][0]["id"]
    time.sleep(0.5)
    assert admin.download(job_id)["state"] == "queued"
    admin.put("/settings", json={"downloads": {"schedule_enabled": False}}).raise_for_status()
    assert admin.wait_state(job_id, "completed")["downloaded_bytes"] == 50_000


def test_closing_schedule_requeues_running_downloads_and_keeps_progress(admin, fileserver):
    data = fileserver.add("long.bin", payload(800_000))
    job_id = admin.submit(fileserver.url("slow", "long.bin"))["created"][0]["id"]
    admin.wait_state(job_id, "downloading")
    time.sleep(0.3)
    admin.put("/settings", json={"downloads": closed_now()}).raise_for_status()
    queued = admin.wait_state(job_id, "queued")
    assert 0 < queued["downloaded_bytes"] < len(data)
    admin.put("/settings", json={"downloads": {"schedule_enabled": False}}).raise_for_status()
    assert admin.wait_state(job_id, "completed", timeout=60)["downloaded_bytes"] == len(data)


def test_bad_times_are_refused(admin):
    response = admin.put("/settings", json={"downloads": {"schedule_start": "25:00"}})
    assert response.status_code == 422
    response = admin.put("/settings", json={"downloads": {"schedule_days": [7]}})
    assert response.status_code == 422
