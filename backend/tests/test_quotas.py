"""Per-user quotas are enforced by the backend."""

import time

from .conftest import payload

UNLIMITED = {
    "max_concurrent": 0, "max_storage_bytes": 0, "max_file_bytes": 0,
    "daily_downloads": 0, "bandwidth_bps": 0, "max_queued": 0,
}


def set_quota(admin, user, **values):
    response = admin.put(f"/users/{user.user['id']}/quota", json={**UNLIMITED, **values})
    assert response.status_code == 200, response.text


def test_new_users_get_the_default_quota(admin, user):
    limits = user.get("/auth/me").json()["quota"]["limits"]
    assert limits["max_storage_bytes"] == 50 * 1024**3
    assert limits["max_queued"] == 100 and limits["max_concurrent"] == 2
    assert admin.get(f"/users/{user.user['id']}/quota").json()["max_queued"] == 100


def test_quota_validation(admin, user):
    response = admin.put(f"/users/{user.user['id']}/quota", json={**UNLIMITED, "max_concurrent": -1})
    assert response.status_code == 422


def test_max_queued_jobs(admin, user, fileserver):
    set_quota(admin, user, max_queued=2)
    admin.put("/settings", json={"downloads": {"max_global_concurrent": 1}})
    for name in "abc":
        fileserver.add(f"{name}.bin", payload(300_000))
    user.submit(fileserver.url("slow", "a.bin"), fileserver.url("slow", "b.bin"))
    response = user.post("/downloads", json={"urls": [fileserver.url("slow", "c.bin")]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "queue_limit"


def test_daily_download_limit(admin, user, fileserver):
    set_quota(admin, user, daily_downloads=2)
    fileserver.add("a.bin", payload(10))
    for _ in range(2):
        user.wait_state(user.submit(fileserver.url("file", "a.bin"))["created"][0]["id"], "completed")
    response = user.post("/downloads", json={"urls": [fileserver.url("file", "a.bin")]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "daily_limit"
    assert user.get("/auth/me").json()["quota"]["usage"]["downloads_today"] == 2


def test_max_file_size_at_submission(admin, user, fileserver):
    set_quota(admin, user, max_file_bytes=1000)
    fileserver.add("big.bin", payload(5000))
    response = user.post("/downloads", json={"urls": [fileserver.url("file", "big.bin")]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "file_too_large"


def test_max_file_size_is_enforced_again_by_the_engine(admin, user, fileserver):
    """Even if a job slips into the queue, the worker refuses an oversized file."""
    fileserver.add("grow.bin", payload(100))
    job_id = user.submit(fileserver.url("file", "grow.bin"))["created"][0]["id"]
    user.wait_state(job_id, "completed")
    set_quota(admin, user, max_file_bytes=1000)
    fileserver.add("grow.bin", payload(50_000))
    ctx = admin.client.app.state.ctx
    with ctx.db.session() as db:
        from fetch_app.models import Download

        db.get(Download, job_id).state = "pending"
    ctx.manager.wake()
    failed = user.wait_state(job_id, "failed")
    assert failed["error_code"] == "file_too_large"


def test_storage_allocation(admin, user, fileserver):
    set_quota(admin, user, max_storage_bytes=10_000)
    fileserver.add("six.bin", payload(6000))
    user.wait_state(user.submit(fileserver.url("file", "six.bin"))["created"][0]["id"], "completed")
    me = user.get("/auth/me").json()["quota"]
    assert me["usage"]["storage_bytes"] == 6000 and me["limits"]["max_storage_bytes"] == 10_000
    response = user.post("/downloads", json={"urls": [fileserver.url("file", "six.bin")]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "storage_quota_exceeded"


def test_storage_allocation_for_unknown_sizes_is_enforced_while_downloading(admin, user, fileserver):
    set_quota(admin, user, max_storage_bytes=10_000)
    fileserver.add("small.bin", payload(100))
    job_id = user.submit(fileserver.url("file", "small.bin"))["created"][0]["id"]
    user.wait_state(job_id, "completed")
    fileserver.add("small.bin", payload(60_000))
    ctx = admin.client.app.state.ctx
    with ctx.db.session() as db:
        from fetch_app.models import Download

        row = db.get(Download, job_id)
        row.state, row.total_bytes = "pending", None
    ctx.manager.wake()
    assert user.wait_state(job_id, "failed")["error_code"] == "storage_quota_exceeded"


def test_per_user_concurrency(admin, user, fileserver):
    admin.put("/settings", json={"downloads": {"max_global_concurrent": 4, "max_per_user_concurrent": 4}})
    set_quota(admin, user, max_concurrent=1)
    for name in "abc":
        fileserver.add(f"{name}.bin", payload(80_000))
    ids = [j["id"] for j in user.submit(*[fileserver.url("slow", f"{n}.bin") for n in "abc"])["created"]]
    admin_job = admin.submit(fileserver.url("slow", "a.bin"))["created"][0]["id"]
    peak = 0
    deadline = time.monotonic() + 30
    saw_both = False
    while time.monotonic() < deadline:
        jobs = [user.download(i) for i in ids]
        running = sum(j["state"] in ("inspecting", "downloading", "verifying") for j in jobs)
        peak = max(peak, running)
        if running and admin.download(admin_job)["state"] == "downloading":
            saw_both = True
        if all(j["state"] == "completed" for j in jobs):
            break
        time.sleep(0.03)
    assert peak == 1, "the user never ran more than one job at a time"
    assert saw_both, "another user's job ran alongside (global slots were free)"


def test_bandwidth_limit(admin, user, fileserver):
    set_quota(admin, user, bandwidth_bps=200_000)
    fileserver.slow_delay = 0
    fileserver.add("fast.bin", payload(500_000))
    started = time.monotonic()
    job = user.wait_state(user.submit(fileserver.url("file", "fast.bin"))["created"][0]["id"], "completed", timeout=30)
    elapsed = time.monotonic() - started
    # 500 kB at 200 kB/s with a one-second burst allowance: at least ~1.2 s.
    assert elapsed > 1.2, f"finished in {elapsed:.2f}s, limiter not applied"
    assert job["avg_speed_bps"] < 450_000
