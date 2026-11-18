"""FETCH's own multi-connection (segmented) engine."""

import os
import time

from fetch_app.engine.segmented import MIN_SPLIT, plan_segments

MB = 1024 * 1024


def stored(boot, api, name):
    return boot.storage_root / "downloads" / str(api.user["id"]) / name


def ranged_gets(fileserver, kind):
    return [h["Range"] for m, p, h in fileserver.requests if m == "GET" and p.startswith(f"/{kind}/") and "Range" in h]


def test_plan_splits_evenly_and_covers_the_file():
    segments = plan_segments(10 * MB + 7, 8)
    assert len(segments) == 8
    assert segments[0].start == 0 and segments[-1].end == 10 * MB + 7
    assert all(a.end == b.start for a, b in zip(segments, segments[1:]))
    # Never more ranges than whole megabytes.
    assert len(plan_segments(3 * MIN_SPLIT, 16)) == 3


def test_large_file_is_fetched_over_several_connections(admin, fileserver, boot):
    data = fileserver.add("big.bin", os.urandom(6 * MB))
    job_id = admin.submit(fileserver.url("file", "big.bin"))["created"][0]["id"]
    done = admin.wait_state(job_id, "completed", timeout=60)
    assert done["engine"] == "segmented"
    assert stored(boot, admin, "big.bin").read_bytes() == data
    ranges = ranged_gets(fileserver, "file")
    assert len(ranges) >= 4, ranges
    assert all("-" in r and not r.endswith("-") for r in ranges), "every range is bounded"


def test_connections_setting_of_one_uses_a_single_stream(admin, fileserver, boot):
    admin.put("/settings", json={"downloads": {"connections_per_download": 1}})
    data = fileserver.add("one.bin", os.urandom(3 * MB))
    job_id = admin.submit(fileserver.url("file", "one.bin"))["created"][0]["id"]
    done = admin.wait_state(job_id, "completed", timeout=60)
    assert done["engine"] == "native"
    assert stored(boot, admin, "one.bin").read_bytes() == data


def test_server_without_ranges_falls_back_to_one_connection(admin, fileserver, boot):
    data = fileserver.add("plain.bin", os.urandom(3 * MB))
    job_id = admin.submit(fileserver.url("norange", "plain.bin"))["created"][0]["id"]
    done = admin.wait_state(job_id, "completed", timeout=60)
    assert done["engine"] == "native"
    assert stored(boot, admin, "plain.bin").read_bytes() == data


def test_pause_and_resume_keeps_every_range(admin, fileserver, boot):
    fileserver.slow_delay = 0.01
    data = fileserver.add("slowbig.bin", os.urandom(4 * MB))
    job_id = admin.submit(fileserver.url("slow", "slowbig.bin"))["created"][0]["id"]
    admin.wait_state(job_id, "downloading")
    deadline = time.monotonic() + 20
    while admin.download(job_id)["downloaded_bytes"] < MB and time.monotonic() < deadline:
        time.sleep(0.05)
    paused = admin.post(f"/downloads/{job_id}/pause").json()
    assert paused["state"] == "paused"
    assert 0 < paused["downloaded_bytes"] < len(data)
    before = len(fileserver.requests)

    fileserver.slow_delay = 0.0
    assert admin.post(f"/downloads/{job_id}/resume").json()["state"] == "queued"
    done = admin.wait_state(job_id, "completed", timeout=60)
    assert done["engine"] == "segmented"
    assert stored(boot, admin, "slowbig.bin").read_bytes() == data
    resumed = [h.get("Range", "") for m, p, h in fileserver.requests[before:] if m == "GET"]
    assert resumed and all(r and not r.startswith("bytes=0-") for r in resumed), (
        "after resuming no range starts again from the beginning"
    )


def test_interrupted_ranges_are_retried(admin, fileserver, boot):
    data = fileserver.add("flaky.bin", os.urandom(3 * MB))
    fileserver.flaky_budget["flaky.bin"] = 3
    job_id = admin.submit(fileserver.url("flaky", "flaky.bin"))["created"][0]["id"]
    done = admin.wait_state(job_id, "completed", timeout=60)
    assert done["engine"] == "segmented"
    assert stored(boot, admin, "flaky.bin").read_bytes() == data
