"""Storage isolation, the file manager and safe streaming."""

import os

import pytest

from fetch_app.storage import (
    StorageError,
    StorageManager,
    clean_rel_path,
    file_type,
    sanitize_filename,
)

from .conftest import payload


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\Windows\\System32\\cmd.exe", "cmd.exe"),
        ("C:\\Users\\me\\file.txt", "file.txt"),
        ("a<b>c:d\"e|f?g*.txt", "a_b_c_d_e_f_g_.txt"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("COM1.tar.gz", "_COM1.tar.gz"),
        ("trailing dots...", "trailing dots"),
        ("  spaced   name .mkv ", "spaced name.mkv"),
        ("", "download"),
        ("...", "download"),
        ("line\nbreak\x00.bin", "line_break_.bin"),
        ("file.txt:hidden-stream", "file.txt_hidden-stream"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_limits_length_and_is_idempotent():
    name = sanitize_filename("x" * 500 + ".mkv")
    assert len(name) <= 200 and name.endswith(".mkv")
    assert sanitize_filename(name) == name


@pytest.mark.parametrize(
    "bad", ["../x", "a/../../b", "/etc/passwd", "C:/Windows", "a/./../..", "..", "a\\..\\..\\b", "x/CON/y", "a/b\x00c"]
)
def test_clean_rel_path_rejects_traversal(bad):
    with pytest.raises(StorageError):
        clean_rel_path(bad)


def test_clean_rel_path_normalises():
    assert clean_rel_path("") == ""
    assert clean_rel_path("a\\b/c") == "a/b/c"
    assert clean_rel_path("./a//b/") == "a/b"


def test_resolve_refuses_symlink_escape(tmp_path):
    storage = StorageManager(tmp_path / "storage")
    storage.ensure()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    root = storage.space_root("private", 1)
    os.symlink(outside, root / "link")
    with pytest.raises(StorageError):
        storage.resolve("private", 1, "link/secret.txt")
    assert [e.name for e in storage.list_dir("private", 1, "")] == []


def test_finalize_is_unique_and_keeps_extension(tmp_path):
    storage = StorageManager(tmp_path / "storage")
    storage.ensure()
    results = []
    for _ in range(3):
        temp = storage.job_temp_dir(1) / "payload"
        temp.write_bytes(b"data")
        results.append(storage.finalize(temp, "private", 7, "sub", "movie.mkv").name)
    assert results == ["movie.mkv", "movie (1).mkv", "movie (2).mkv"]
    assert storage.usage("private", 7) == 12


def test_file_types():
    assert file_type("a.MKV") == "video" and file_type("b.zip") == "archive" and file_type("c") == "other"


# -- API level -------------------------------------------------------------------


@pytest.fixture
def populated(admin, fileserver):
    """``admin.bin`` in the local profile's folder and one file in the shared folder."""
    fileserver.add("admin.bin", payload(1000))
    fileserver.add("shared.bin", payload(3000))
    admin.wait_state(admin.submit(fileserver.url("file", "admin.bin"))["created"][0]["id"], "completed")
    admin.wait_state(
        admin.submit(fileserver.url("file", "shared.bin"), destination="shared")["created"][0]["id"], "completed"
    )
    return admin, None, fileserver


def test_listing_shows_downloaded_files_with_their_source(populated):
    admin, _, _ = populated
    listing = admin.get("/files").json()
    assert [i["name"] for i in listing["items"]] == ["admin.bin"]
    item = listing["items"][0]
    assert item["size"] == 1000 and item["provider"] == "direct" and item["downloaded_at"]
    assert item["source_url"].endswith("/file/admin.bin")


def test_paths_cannot_leave_the_storage_folder(populated):
    admin, _, _ = populated
    for attempt in ("../0/admin.bin", "..%2Fshared%2Fshared.bin", "/etc/passwd", "..\\..\\data\\fetch.db"):
        assert admin.get("/files/download", params={"path": attempt}).status_code == 404
        assert admin.get("/files", params={"path": attempt}).status_code == 404
        assert admin.delete("/files", params={"path": attempt}).status_code == 404
    assert admin.get("/files/download", params={"path": "admin.bin"}).status_code == 200


def test_reveal_shows_files_in_the_file_manager_without_leaving_storage(populated, monkeypatch):
    from fetch_app.api import files

    admin, _, _ = populated
    shown = []
    monkeypatch.setattr(files, "open_in_file_manager", shown.append)
    assert admin.post("/files/reveal", json={"path": "admin.bin"}).json() == {"ok": True}
    assert admin.post("/files/reveal", json={"path": ""}).status_code == 200
    assert shown[0].name == "admin.bin" and shown[0].is_file()
    assert shown[1].is_dir() and shown[1] == shown[0].parent
    for attempt in ("../0/shared.bin", "/etc/passwd", "missing.bin"):
        assert admin.post("/files/reveal", json={"path": attempt}).status_code == 404
    assert len(shown) == 2


def test_shared_folder_of_earlier_versions_stays_reachable(populated):
    admin, _, _ = populated
    listing = admin.get("/files", params={"scope": "shared"}).json()
    assert [i["name"] for i in listing["items"]] == ["shared.bin"]
    assert admin.get("/files/usage").json()["shared"]["used_bytes"] == 3000
    assert admin.get("/files/download", params={"scope": "shared", "path": "shared.bin"}).status_code == 200
    assert admin.delete("/files", params={"scope": "shared", "path": "shared.bin"}).status_code == 200


def test_download_is_always_an_attachment(populated):
    admin, _, fileserver = populated
    response = admin.get("/files/download", params={"path": "admin.bin"})
    assert response.content == fileserver.files["admin.bin"]
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_range_requests_on_stored_files(populated):
    admin, _, fileserver = populated
    data = fileserver.files["admin.bin"]
    response = admin.get("/files/download", params={"path": "admin.bin"}, headers={"Range": "bytes=100-199"})
    assert response.status_code == 206
    assert response.content == data[100:200]
    assert response.headers["content-range"] == f"bytes 100-199/{len(data)}"


def test_large_file_is_streamed_not_buffered(admin, boot):
    """A 2 GiB sparse file is served in bounded chunks."""
    root = boot.storage_root / "downloads" / str(admin.user["id"])
    root.mkdir(parents=True, exist_ok=True)
    size = 2 * 1024**3
    with open(root / "huge.bin", "wb") as fh:
        fh.truncate(size)
    import tracemalloc

    # Talk to the ASGI app directly: the test client's own transport may
    # buffer a whole response, which would hide how the server sends it.
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/api/files/download", "raw_path": b"/api/files/download", "root_path": "",
        "query_string": b"path=huge.bin", "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 50000),
        "headers": [(b"host", b"127.0.0.1:8787")],
    }
    seen = {"status": 0, "length": 0, "received": 0, "largest": 0}

    class Enough(Exception):
        pass

    import asyncio

    requested = []

    async def receive():
        if not requested:
            requested.append(True)
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # the client stays connected and sends nothing more

    async def send(message):
        if message["type"] == "http.response.start":
            seen["status"] = message["status"]
            seen["length"] = int(dict(message["headers"])[b"content-length"])
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            seen["received"] += len(body)
            seen["largest"] = max(seen["largest"], len(body))
            if seen["received"] >= 256 * 1024 * 1024:
                raise Enough  # like a client that disconnects

    async def run():
        try:
            await admin.client.app(scope, receive, send)
        except BaseException as error:  # Enough, possibly wrapped in an exception group
            if seen["received"] < 256 * 1024 * 1024:
                raise error

    tracemalloc.start()
    admin.client.portal.call(run)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert seen["status"] == 200
    assert seen["length"] == size
    received, largest = seen["received"], seen["largest"]
    assert received >= 256 * 1024 * 1024
    assert largest <= 4 * 1024 * 1024
    assert peak < 64 * 1024 * 1024, f"peak traced memory {peak} bytes"
    tail = admin.get("/files/download", params={"path": "huge.bin"}, headers={"Range": f"bytes={size - 10}-"})
    assert tail.status_code == 206 and tail.content == b"\0" * 10


def test_large_download_is_written_incrementally(admin, fileserver, boot):
    """Downloading stays memory-bounded: 48 MiB arrives through a 256 KiB pipeline."""
    fileserver.slow_delay = 0
    data = fileserver.add("large.bin", os.urandom(1024 * 1024) * 48)
    job = admin.wait_state(admin.submit(fileserver.url("file", "large.bin"))["created"][0]["id"], "completed", timeout=60)
    target = boot.storage_root / "downloads" / str(admin.user["id"]) / job["filename"]
    assert target.stat().st_size == len(data)
    import hashlib

    digest = hashlib.sha256()
    with target.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    assert digest.hexdigest() == hashlib.sha256(data).hexdigest()


def test_folders_search_sort_filter_and_delete(admin, fileserver):
    for name, size in (("b.mkv", 300), ("a.zip", 200), ("c.txt", 100)):
        fileserver.add(name, payload(size))
        admin.wait_state(admin.submit(fileserver.url("file", name), folder="media")["created"][0]["id"], "completed")
    assert admin.post("/files/folder", json={"path": "", "name": "empty"}).status_code == 201
    assert admin.post("/files/folder", json={"path": "", "name": "empty"}).status_code == 422
    assert admin.post("/files/folder", json={"path": "", "name": "../up"}).status_code == 422
    assert admin.post("/files/folder", json={"path": "", "name": "CON"}).status_code == 422

    root = admin.get("/files").json()["items"]
    assert [(i["name"], i["is_dir"]) for i in root] == [("empty", True), ("media", True)]
    media = admin.get("/files", params={"path": "media", "sort": "size", "order": "desc"}).json()["items"]
    assert [i["name"] for i in media] == ["b.mkv", "a.zip", "c.txt"]
    assert [i["name"] for i in admin.get("/files", params={"path": "media", "type": "video"}).json()["items"]] == ["b.mkv"]
    found = admin.get("/files", params={"q": "ZIP"}).json()["items"]
    assert [i["path"] for i in found] == ["media/a.zip"]

    assert admin.delete("/files", params={"path": "media/a.zip"}).json()["freed_bytes"] == 200
    assert admin.delete("/files", params={"path": "media"}).status_code == 200
    assert admin.delete("/files", params={"path": ""}).status_code in (404, 422)
    assert [i["name"] for i in admin.get("/files").json()["items"]] == ["empty"]
    usage = admin.get("/files/usage").json()
    assert usage["private"]["used_bytes"] == 0 and "disk" in usage


def test_usage_reports_the_storage_limit(populated):
    admin, _, _ = populated
    usage = admin.get("/files/usage").json()
    assert usage["private"] == {"used_bytes": 1000, "limit_bytes": 0}
    admin.put("/settings", json={"storage": {"max_storage_gb": 2}})
    assert admin.get("/files/usage").json()["private"]["limit_bytes"] == 2 * 1024**3
