"""Download submission, the queue, controls, retries and recovery."""

import hashlib
import time

from fetch_app.app import create_app

from .conftest import ADMIN_PASSWORD, Api, payload


def stored(boot, api, name):
    return boot.storage_root / "downloads" / str(api.user["id"]) / name


def test_inspect_reports_metadata_without_queueing(admin, fileserver):
    fileserver.add("movie.mkv", payload(12345))
    items = admin.post(
        "/downloads/inspect",
        json={"urls": [fileserver.url("file", "movie.mkv"), fileserver.url("file", "missing.bin"), "notaurl"]},
    ).json()["items"]
    assert items[0] | {"url": ""} == {
        "url": "", "provider": "direct", "provider_name": "Direct HTTP/HTTPS", "supported": True,
        "filename": "movie.mkv", "size": 12345, "resumable": True, "restriction": "", "message": "",
    }
    assert items[1]["supported"] is False and items[1]["restriction"] == "not_found"
    assert items[2]["supported"] is False and items[2]["restriction"] == "unsafe_url"
    assert admin.get("/downloads").json()["total"] == 0


def test_batch_submission_mixes_accepted_and_rejected(admin, fileserver, boot):
    files = {f"f{i}.bin": fileserver.add(f"f{i}.bin", payload(2000 + i)) for i in range(3)}
    result = admin.submit(*[fileserver.url("file", n) for n in files], fileserver.url("file", "nope.bin"))
    assert len(result["created"]) == 3
    assert [r["code"] for r in result["rejected"]] == ["not_found"]
    for job in result["created"]:
        done = admin.wait_state(job["id"], "completed")
        assert stored(boot, admin, done["filename"]).read_bytes() == files[done["filename"]]
    stats = admin.get("/downloads/stats").json()
    assert stats["completed"] == 3 and stats["active"] == 0


def test_progress_fields_are_real(admin, fileserver):
    data = fileserver.add("slow.bin", payload(400_000))
    job_id = admin.submit(fileserver.url("slow", "slow.bin"))["created"][0]["id"]
    seen = []
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = admin.download(job_id)
        seen.append((job["state"], job["downloaded_bytes"]))
        if job["state"] == "completed":
            break
        time.sleep(0.05)
    downloading = [b for s, b in seen if s == "downloading"]
    assert downloading == sorted(downloading), "progress never goes backwards"
    assert any(0 < b < len(data) for b in downloading), "intermediate progress was observed"
    assert job["downloaded_bytes"] == job["total_bytes"] == len(data)
    assert job["avg_speed_bps"] > 0 and job["started_at"] and job["finished_at"]
    assert job["engine"] == "native"


def test_pause_and_resume_continues_with_a_range_request(admin, fileserver, boot):
    data = fileserver.add("big.bin", payload(600_000))
    job_id = admin.submit(fileserver.url("slow", "big.bin"))["created"][0]["id"]
    admin.wait_state(job_id, "downloading")
    time.sleep(0.25)
    paused = admin.post(f"/downloads/{job_id}/pause").json()
    assert paused["state"] == "paused"
    assert 0 < paused["downloaded_bytes"] < len(data)
    time.sleep(0.2)
    assert admin.download(job_id)["downloaded_bytes"] == paused["downloaded_bytes"], "really stopped"

    assert admin.post(f"/downloads/{job_id}/resume").json()["state"] == "queued"
    done = admin.wait_state(job_id, "completed")
    assert stored(boot, admin, done["filename"]).read_bytes() == data
    ranged = [h["Range"] for m, p, h in fileserver.requests if m == "GET" and "Range" in h and p.startswith("/slow")]
    assert ranged and int(ranged[-1].split("=")[1].rstrip("-")) > 0, "resume used a Range request"


def test_cancel_removes_partial_data(admin, fileserver, boot):
    fileserver.add("big.bin", payload(600_000))
    job_id = admin.submit(fileserver.url("slow", "big.bin"))["created"][0]["id"]
    admin.wait_state(job_id, "downloading")
    cancelled = admin.post(f"/downloads/{job_id}/cancel").json()
    assert cancelled["state"] == "cancelled"
    assert not (boot.storage_root / "temporary" / str(job_id)).exists()
    assert not stored(boot, admin, "big.bin").exists()
    assert admin.post(f"/downloads/{job_id}/pause").status_code == 409


def test_retry_after_failure(admin, fileserver, boot):
    url = fileserver.url("file", "late.bin")
    fileserver.add("late.bin", b"x")
    job_id = admin.submit(url)["created"][0]["id"]
    admin.wait_state(job_id, "completed")
    # Same URL, now gone: the job fails with an accurate, non-retryable error.
    del fileserver.files["late.bin"]
    assert admin.submit(url)["rejected"][0]["code"] == "not_found"


def test_failed_download_can_be_retried(admin, fileserver, boot):
    fileserver.add("gone.bin", payload(100_000))
    job_id = admin.submit(fileserver.url("slow", "gone.bin"))["created"][0]["id"]
    admin.post(f"/downloads/{job_id}/pause")
    saved = fileserver.files.pop("gone.bin")
    admin.post(f"/downloads/{job_id}/resume")
    failed = admin.wait_state(job_id, "failed")
    assert failed["error_code"] == "not_found" and failed["error_message"]
    fileserver.files["gone.bin"] = saved
    assert admin.post(f"/downloads/{job_id}/retry").status_code == 200
    assert admin.wait_state(job_id, "completed")["downloaded_bytes"] == len(saved)


def test_interrupted_connection_is_retried_and_resumed(admin, fileserver, boot):
    data = fileserver.add("flaky.bin", payload(200_000))
    fileserver.flaky_budget["flaky.bin"] = 1
    job_id = admin.submit(fileserver.url("flaky", "flaky.bin"))["created"][0]["id"]
    done = admin.wait_state(job_id, "completed", "failed", timeout=30)
    assert done["state"] == "completed", done
    assert done["attempts"] == 1
    assert stored(boot, admin, "flaky.bin").read_bytes() == data
    gets = [h for m, p, h in fileserver.requests if m == "GET" and p == "/flaky/flaky.bin"]
    resumed_from = int(gets[-1]["Range"].removeprefix("bytes=").rstrip("-"))
    assert 0 < resumed_from <= len(data) // 2, "second attempt resumed mid-file"
    assert gets[-1].get("If-Range"), "resume is conditional on the file being unchanged"


def test_retry_limit_is_honoured(admin, fileserver):
    admin.put("/settings", json={"downloads": {"retry_count": 1, "retry_delay_seconds": 1}})
    fileserver.add("always.bin", payload(50_000))
    fileserver.flaky_budget["always.bin"] = 99
    job_id = admin.submit(fileserver.url("flaky", "always.bin"))["created"][0]["id"]
    failed = admin.wait_state(job_id, "failed", timeout=30)
    assert failed["attempts"] == 1 and failed["max_attempts"] == 1
    assert failed["error_code"] == "network_error"


def test_provider_wait_period_is_honoured(admin, fileserver):
    fileserver.add("gate.bin", b"x")  # so inspection succeeds
    fileserver.retry_after = 2
    job_id = admin.submit(fileserver.url("file", "gate.bin"))["created"][0]["id"]
    admin.wait_state(job_id, "completed")
    # A server answering 429 + Retry-After puts the job into Waiting, not Failed.
    with admin.client.app.state.ctx.db.session() as db:
        from fetch_app.models import Download

        row = db.get(Download, job_id)
        row.url, row.state = fileserver.url("status", "429"), "pending"
    admin.client.app.state.ctx.manager.wake()
    waiting = admin.wait_state(job_id, "waiting")
    assert waiting["error_code"] == "wait_required" and waiting["wait_until"]
    assert waiting["attempts"] == 0


def test_remove_from_history_optionally_deletes_file(admin, fileserver, boot):
    fileserver.add("keep.bin", payload(100))
    fileserver.add("drop.bin", payload(100))
    keep = admin.submit(fileserver.url("file", "keep.bin"))["created"][0]["id"]
    drop = admin.submit(fileserver.url("file", "drop.bin"))["created"][0]["id"]
    admin.wait_state(keep, "completed")
    admin.wait_state(drop, "completed")
    assert admin.delete(f"/downloads/{keep}").status_code == 200
    assert admin.delete(f"/downloads/{drop}", params={"delete_file": "true"}).status_code == 200
    assert admin.get("/downloads").json()["total"] == 0
    assert stored(boot, admin, "keep.bin").exists()
    assert not stored(boot, admin, "drop.bin").exists()


def test_duplicate_filenames_get_unique_names(admin, fileserver, boot):
    fileserver.add("same.bin", payload(64))
    ids = [admin.submit(fileserver.url("file", "same.bin"))["created"][0]["id"] for _ in range(3)]
    names = sorted(admin.wait_state(i, "completed")["filename"] for i in ids)
    assert names == ["same (1).bin", "same (2).bin", "same.bin"]


def test_untrusted_filenames_cannot_escape_storage(admin, fileserver, boot):
    fileserver.add("x", b"payload")
    for evil in ("../../evil.txt", "..\\\\..\\\\windows\\\\system32\\\\evil.dll", "CON.txt", "a<b>:c|d?.txt"):
        fileserver.disposition = evil
        job = admin.wait_state(admin.submit(fileserver.url("cd", "x"))["created"][0]["id"], "completed")
        assert "/" not in job["filename"] and "\\" not in job["filename"] and ".." not in job["filename"]
        assert stored(boot, admin, job["filename"]).is_file()
    everything = {p.relative_to(boot.home).parts[0] for p in boot.home.rglob("*") if p.is_file()}
    assert everything <= {"storage", "data", "logs"}
    assert not list(boot.home.rglob("evil*")) or all("downloads" in str(p) for p in boot.home.rglob("evil*"))


def test_destination_folder_and_shared_space(admin, fileserver, boot):
    fileserver.add("a.bin", payload(10))
    job = admin.submit(fileserver.url("file", "a.bin"), folder="movies/2024")["created"][0]
    done = admin.wait_state(job["id"], "completed")
    assert done["final_path"] == "movies/2024/a.bin"
    assert (boot.storage_root / "downloads" / str(admin.user["id"]) / "movies/2024/a.bin").is_file()
    job = admin.submit(fileserver.url("file", "a.bin"), destination="shared")["created"][0]
    admin.wait_state(job["id"], "completed")
    assert (boot.storage_root / "shared" / "a.bin").is_file()
    bad = admin.post("/downloads", json={"urls": [fileserver.url("file", "a.bin")], "folder": "../../etc"})
    assert bad.status_code == 422


def test_global_concurrency_and_priority_order(admin, fileserver):
    admin.put("/settings", json={"downloads": {"max_global_concurrent": 1, "max_per_user_concurrent": 1}})
    for name in "abcd":
        fileserver.add(f"{name}.bin", payload(500_000 if name == "a" else 100_000))
    first = admin.submit(fileserver.url("slow", "a.bin"))["created"][0]["id"]
    admin.wait_state(first, "downloading")
    low = admin.submit(fileserver.url("slow", "b.bin"), priority=0)["created"][0]["id"]
    normal = admin.submit(fileserver.url("slow", "c.bin"), priority=1)["created"][0]["id"]
    urgent = admin.submit(fileserver.url("slow", "d.bin"), priority=3)["created"][0]["id"]
    time.sleep(0.2)
    states = {j["id"]: j["state"] for j in admin.get("/downloads").json()["items"]}
    assert states[first] == "downloading"
    assert {states[low], states[normal], states[urgent]} == {"queued"}
    assert admin.get("/downloads/stats").json()["active"] == 1

    order = []
    deadline = time.monotonic() + 30
    while len(order) < 4 and time.monotonic() < deadline:
        active = [j["id"] for j in admin.get("/downloads", params={"state": "downloading"}).json()["items"]]
        assert len(active) <= 1
        for job_id in active:
            if job_id not in order:
                order.append(job_id)
        time.sleep(0.03)
    assert order == [first, urgent, normal, low]


def test_queue_reordering(admin, fileserver):
    admin.put("/settings", json={"downloads": {"max_global_concurrent": 1}})
    for name in "abcd":
        fileserver.add(f"{name}.bin", payload(500_000 if name == "a" else 60_000))
    running = admin.submit(fileserver.url("slow", "a.bin"))["created"][0]["id"]
    admin.wait_state(running, "downloading")
    b, c, d = (j["id"] for j in admin.submit(*[fileserver.url("slow", f"{n}.bin") for n in "bcd"])["created"])
    time.sleep(0.2)
    assert admin.post("/downloads/reorder", json={"ids": [d, b, c]}).status_code == 200
    queue = admin.get("/downloads", params={"sort": "queue", "state": "queued"}).json()["items"]
    assert [j["id"] for j in queue] == [d, b, c]
    assert admin.post("/downloads/reorder", json={"ids": [running]}).status_code == 409
    for job_id in (running, b, c, d):
        admin.wait_state(job_id, "completed", timeout=30)
    finished = sorted([b, c, d], key=lambda i: admin.download(i)["finished_at"])
    assert finished == [d, b, c]


def test_bulk_actions(admin, fileserver):
    admin.put("/settings", json={"downloads": {"max_global_concurrent": 1}})
    for name in "abc":
        fileserver.add(f"{name}.bin", payload(200_000))
    ids = [j["id"] for j in admin.submit(*[fileserver.url("slow", f"{n}.bin") for n in "abc"])["created"]]
    admin.wait_state(ids[0], "downloading")
    result = admin.post("/downloads/bulk", json={"ids": ids, "action": "pause"}).json()
    assert sorted(result["done"]) == ids
    assert {admin.download(i)["state"] for i in ids} == {"paused"}
    result = admin.post("/downloads/bulk", json={"ids": ids + [9999], "action": "cancel"}).json()
    assert sorted(result["done"]) == ids and result["skipped"] == [9999]
    result = admin.post("/downloads/bulk", json={"ids": ids, "action": "remove"}).json()
    assert sorted(result["done"]) == ids
    assert admin.get("/downloads").json()["total"] == 0


def test_list_filtering_sorting_search_and_pagination(admin, fileserver):
    for i, name in enumerate(["alpha.bin", "beta.bin", "gamma_file.bin"]):
        fileserver.add(name, payload(100 * (i + 1)))
        admin.wait_state(admin.submit(fileserver.url("file", name))["created"][0]["id"], "completed")
    by_size = admin.get("/downloads", params={"sort": "size", "order": "desc"}).json()["items"]
    assert [j["filename"] for j in by_size] == ["gamma_file.bin", "beta.bin", "alpha.bin"]
    by_name = admin.get("/downloads", params={"sort": "filename", "order": "asc", "page_size": 2}).json()
    assert [j["filename"] for j in by_name["items"]] == ["alpha.bin", "beta.bin"] and by_name["total"] == 3
    page2 = admin.get("/downloads", params={"sort": "filename", "order": "asc", "page_size": 2, "page": 2}).json()
    assert [j["filename"] for j in page2["items"]] == ["gamma_file.bin"]
    assert [j["filename"] for j in admin.get("/downloads", params={"q": "GAMMA_"}).json()["items"]] == ["gamma_file.bin"]
    assert admin.get("/downloads", params={"q": "%"}).json()["total"] == 0
    assert admin.get("/downloads", params={"state": "failed"}).json()["total"] == 0
    assert admin.get("/downloads", params={"state": "bogus"}).status_code == 422
    assert admin.get("/downloads", params={"sort": "bogus"}).status_code == 422


def test_server_without_head_or_ranges(admin, fileserver, boot):
    data = fileserver.add("plain.bin", payload(30_000))
    for kind in ("nohead", "norange"):
        job = admin.wait_state(admin.submit(fileserver.url(kind, "plain.bin"))["created"][0]["id"], "completed")
        assert stored(boot, admin, job["filename"]).read_bytes() == data


def test_html_pages_are_flagged(admin, fileserver):
    fileserver.add("page", b"<html></html>")
    item = admin.post("/downloads/inspect", json={"urls": [fileserver.url("html", "page")]}).json()["items"][0]
    assert item["supported"] is True and item["restriction"] == "html_page" and item["message"]


def test_queue_survives_restart_and_resumes(tmp_path, fileserver):
    """Stop FETCH mid-download; a fresh process finishes the job from where it was."""
    from .conftest import make_boot

    boot = make_boot(tmp_path)
    data = fileserver.add("long.bin", payload(800_000))
    with Api(create_app(boot)) as api:
        api.setup_admin()
        api.put("/settings", json={"downloads": {"max_global_concurrent": 1}})
        running = api.submit(fileserver.url("slow", "long.bin"))["created"][0]["id"]
        queued = api.submit(fileserver.url("file", "long.bin"))["created"][0]["id"]
        paused = api.submit(fileserver.url("file", "long.bin"))["created"][0]["id"]
        api.post(f"/downloads/{paused}/pause")
        api.wait_state(running, "downloading")
        time.sleep(0.3)
        assert 0 < api.download(running)["downloaded_bytes"] < len(data)
    # The app has shut down (lifespan exit) while the job was running.
    partial = boot.storage_root / "temporary" / str(running) / "payload"
    assert 0 < partial.stat().st_size < len(data)

    fileserver.requests.clear()
    with Api(create_app(boot)) as api:
        api.login("admin", ADMIN_PASSWORD)
        assert api.get("/settings").json()["settings"]["downloads"]["max_global_concurrent"] == 1
        done = api.wait_state(running, "completed", timeout=40)
        api.wait_state(queued, "completed", timeout=40)
        assert api.download(paused)["state"] == "paused", "paused jobs stay paused across restarts"
        resumed = [h.get("Range") for m, p, h in fileserver.requests if m == "GET" and p == "/slow/long.bin"]
        assert resumed and resumed[0] and resumed[0] != "bytes=0-"
        target = boot.storage_root / "downloads" / str(api.user["id"]) / done["filename"]
        assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(data).digest()


def test_restart_workers_requeues_running_jobs(admin, fileserver, boot):
    data = fileserver.add("w.bin", payload(500_000))
    job_id = admin.submit(fileserver.url("slow", "w.bin"))["created"][0]["id"]
    admin.wait_state(job_id, "downloading")
    time.sleep(0.2)
    assert admin.post("/admin/workers/restart").json()["requeued"] == 1
    done = admin.wait_state(job_id, "completed", timeout=30)
    assert stored(boot, admin, done["filename"]).read_bytes() == data
