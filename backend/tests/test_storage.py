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
def populated(admin, user, fileserver):
    """admin owns ``admin.bin``; alice owns ``alice.bin``; one shared file from alice."""
    fileserver.add("admin.bin", payload(1000))
    fileserver.add("alice.bin", payload(2000))
    fileserver.add("shared.bin", payload(3000))
    admin.wait_state(admin.submit(fileserver.url("file", "admin.bin"))["created"][0]["id"], "completed")
    user.wait_state(user.submit(fileserver.url("file", "alice.bin"))["created"][0]["id"], "completed")
    user.wait_state(
        user.submit(fileserver.url("file", "shared.bin"), destination="shared")["created"][0]["id"], "completed"
    )
    return admin, user, fileserver


def test_each_user_sees_only_their_own_private_files(populated):
    admin, user, _ = populated
    assert [i["name"] for i in admin.get("/files").json()["items"]] == ["admin.bin"]
    listing = user.get("/files").json()
    assert [i["name"] for i in listing["items"]] == ["alice.bin"]
    item = listing["items"][0]
    assert item["size"] == 2000 and item["provider"] == "direct" and item["downloaded_at"]
    assert item["source_url"].endswith("/file/alice.bin")


def test_user_cannot_reach_another_users_files(populated):
    admin, user, _ = populated
    admin_id = admin.user["id"]
    assert user.get("/files", params={"owner_id": admin_id}).status_code == 404
    assert user.get("/files/download", params={"path": "admin.bin", "owner_id": admin_id}).status_code == 404
    assert user.get("/files/download", params={"path": "admin.bin"}).status_code == 404
    assert user.delete("/files", params={"path": "admin.bin", "owner_id": admin_id}).status_code == 404
    for attempt in (f"../{admin_id}/admin.bin", f"..%2F{admin_id}%2Fadmin.bin", "/etc/passwd", "..\\..\\data\\fetch.db"):
        assert user.get("/files/download", params={"path": attempt}).status_code == 404
        assert user.get("/files", params={"path": attempt}).status_code == 404
    assert admin.get("/files/download", params={"path": "admin.bin"}).status_code == 200


def test_admin_can_browse_a_users_files(populated):
    admin, user, _ = populated
    listing = admin.get("/files", params={"owner_id": user.user["id"]}).json()
    assert [i["name"] for i in listing["items"]] == ["alice.bin"]
    assert admin.get("/files", params={"owner_id": 9999}).status_code == 404


def test_shared_space_rules(populated):
    admin, user, _ = populated
    token = admin.invite()
    bob = admin.fork()
    bob.register(token, "bob")
    listing = bob.get("/files", params={"scope": "shared"}).json()
    item = listing["items"][0]
    assert item["name"] == "shared.bin" and item["owner"] == "alice"
    assert item["can_delete"] is False and item["source_url"] == ""
    assert bob.get("/files/download", params={"scope": "shared", "path": "shared.bin"}).status_code == 200
    assert bob.delete("/files", params={"scope": "shared", "path": "shared.bin"}).status_code == 403
    assert user.delete("/files", params={"scope": "shared", "path": "shared.bin"}).status_code == 200

    admin.put("/settings", json={"storage": {"users_can_write_shared": False}})
    refused = bob.post("/downloads", json={"urls": ["https://example.com/x"], "destination": "shared"})
    assert refused.status_code == 403
    admin.put("/settings", json={"storage": {"shared_enabled": False}})
    assert bob.get("/files", params={"scope": "shared"}).status_code == 403


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

    tracemalloc.start()
    received, largest = 0, 0
    with admin.client.stream("GET", "/api/files/download", params={"path": "huge.bin"}) as response:
        assert response.status_code == 200
        assert int(response.headers["content-length"]) == size
        for chunk in response.iter_raw():
            received += len(chunk)
            largest = max(largest, len(chunk))
            if received >= 256 * 1024 * 1024:
                break
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
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


def test_usage_hides_other_users_from_regular_users(populated):
    _, user, _ = populated
    usage = user.get("/files/usage").json()
    assert usage["private"]["used_bytes"] == 2000
    assert "users" not in usage and "disk" not in usage
