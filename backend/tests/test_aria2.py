"""aria2 back-end and its SSRF guard proxy (skipped when aria2c is absent)."""

import asyncio
import base64
import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest

from fetch_app.engine.aria2 import find_aria2
from fetch_app.engine.guardproxy import GuardProxy
from fetch_app.security.urlguard import UrlGuard

from .conftest import payload

ARIA2 = find_aria2() or shutil.which("aria2c", path=str(Path(sys.executable).parent))
needs_aria2 = pytest.mark.skipif(not ARIA2, reason="aria2c is not installed")


@needs_aria2
@pytest.mark.aria2
def test_large_download_uses_aria2_through_the_guard_proxy(admin, fileserver, boot):
    health = {c["name"]: c for c in admin.get("/admin/health").json()["checks"]}
    assert health["aria2 engine"]["ok"], health["aria2 engine"]
    admin.put("/settings", json={"downloads": {"aria2_min_size_mb": 1}})
    fileserver.slow_delay = 0
    data = fileserver.add("big.bin", os.urandom(1024 * 1024) * 6)
    job = admin.wait_state(admin.submit(fileserver.url("file", "big.bin"))["created"][0]["id"], "completed", "failed", timeout=60)
    assert job["state"] == "completed", job
    assert job["engine"] == "aria2"
    target = boot.storage_root / "downloads" / str(admin.user["id"]) / "big.bin"
    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(data).digest()
    ranged = [h for m, p, h in fileserver.requests if m == "GET" and "Range" in h]
    assert len(ranged) >= 2, "aria2 split the file over several connections"
    small = fileserver.add("small.bin", payload(2000))
    job = admin.wait_state(admin.submit(fileserver.url("file", "small.bin"))["created"][0]["id"], "completed")
    assert job["engine"] == "native" and job["total_bytes"] == len(small)


@needs_aria2
@pytest.mark.aria2
def test_aria2_pause_and_resume(admin, fileserver, boot):
    admin.put("/settings", json={"downloads": {"aria2_min_size_mb": 1}})
    data = fileserver.add("slow.bin", os.urandom(1024 * 1024) * 3)
    job_id = admin.submit(fileserver.url("slow", "slow.bin"))["created"][0]["id"]
    admin.wait_state(job_id, "downloading")
    import time

    time.sleep(1.0)
    paused = admin.post(f"/downloads/{job_id}/pause").json()
    assert paused["state"] == "paused" and 0 < paused["downloaded_bytes"] < len(data)
    fileserver.slow_delay = 0
    admin.post(f"/downloads/{job_id}/resume")
    job = admin.wait_state(job_id, "completed", "failed", timeout=60)
    assert job["state"] == "completed" and job["engine"] == "aria2"
    target = boot.storage_root / "downloads" / str(admin.user["id"]) / "slow.bin"
    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(data).digest()


@needs_aria2
@pytest.mark.aria2
def test_aria2_rpc_is_private(admin, app):
    aria2 = app.state.ctx.aria2
    assert aria2.available
    conf = (app.state.ctx.boot.data_dir / "aria2.conf").read_text()
    assert "rpc-listen-all=false" in conf and "proxy-method=tunnel" in conf
    assert admin.client.get("/jsonrpc").status_code in (404, 503)
    assert admin.client.post("/api/jsonrpc", json={}).status_code == 404


async def _connect(proxy: GuardProxy, target: str, password: str | None = None) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    auth = base64.b64encode(f"{proxy.username}:{password or proxy.password}".encode()).decode()
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode())
    await writer.drain()
    status = (await reader.readline()).decode()
    writer.close()
    return status


def test_guard_proxy_refuses_private_targets_and_bad_credentials(fileserver):
    async def scenario():
        strict = GuardProxy(UrlGuard())
        await strict.start()
        try:
            for target in ("127.0.0.1:80", f"127.0.0.1:{fileserver.port}", "10.0.0.1:443", "localhost:80",
                           "169.254.169.254:80", "[::1]:80"):
                assert " 403 " in await _connect(strict, target), target
            assert strict.denied == 6
        finally:
            await strict.stop()

        relaxed = GuardProxy(UrlGuard(["127.0.0.1"]))
        await relaxed.start()
        try:
            assert " 200 " in await _connect(relaxed, f"127.0.0.1:{fileserver.port}")
            assert " 407 " in await _connect(relaxed, f"127.0.0.1:{fileserver.port}", password="wrong")
            reader, writer = await asyncio.open_connection("127.0.0.1", relaxed.port)
            writer.write(b"GET http://127.0.0.1/ HTTP/1.1\r\n\r\n")
            assert b" 405 " in await reader.readline()
            writer.close()
        finally:
            await relaxed.stop()

    asyncio.run(scenario())
