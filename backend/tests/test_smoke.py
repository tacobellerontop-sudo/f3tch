from .conftest import payload


def test_direct_download_end_to_end(admin, fileserver, boot):
    data = fileserver.add("hello.bin", payload(300_000))
    result = admin.submit(fileserver.url("file", "hello.bin"))
    assert result["rejected"] == []
    job = admin.wait_state(result["created"][0]["id"], "completed", "failed")
    assert job["state"] == "completed", job
    assert job["downloaded_bytes"] == len(data)
    stored = boot.storage_root / "downloads" / str(admin.user["id"]) / "hello.bin"
    assert stored.read_bytes() == data
