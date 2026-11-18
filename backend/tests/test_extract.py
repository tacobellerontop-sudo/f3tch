"""Automatic extraction of downloaded archives."""

import io
import shutil
import subprocess
import tarfile
import time
import zipfile

import pytest

from fetch_app.extract import ExtractError, archive_set, extract, find_7zip

from .conftest import payload


def zip_bytes(files: dict[str, bytes], *, raw_names=False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            archive.writestr(info, data)
    return buffer.getvalue()


def test_recognises_archives_and_sets(tmp_path):
    assert archive_set(tmp_path / "a.zip").kind == "zip"
    assert archive_set(tmp_path / "a.tar.gz").folder_name == "a"
    part = archive_set(tmp_path / "movie.part03.rar")
    assert part.head.name == "movie.part01.rar" and part.pattern.match("movie.part12.rar")
    assert not part.pattern.match("movie.rar")
    split = archive_set(tmp_path / "big.7z.002")
    assert split.head.name == "big.7z.001" and split.folder_name == "big"
    old = archive_set(tmp_path / "set.r04")
    assert old.head.name == "set.rar" and old.pattern.match("set.r00")
    assert archive_set(tmp_path / "movie.mkv") is None


def test_zip_is_unpacked_next_to_it(tmp_path):
    (tmp_path / "game.zip").write_bytes(zip_bytes({"game/readme.txt": b"hi", "data.bin": b"x" * 10}))
    target = extract(archive_set(tmp_path / "game.zip"))
    assert target == tmp_path / "game"
    assert (target / "game" / "readme.txt").read_bytes() == b"hi"
    again = extract(archive_set(tmp_path / "game.zip"))
    assert again.name == "game (2)", "an existing folder is never overwritten"


def test_zip_slip_is_refused(tmp_path):
    (tmp_path / "evil.zip").write_bytes(zip_bytes({"../../outside.txt": b"x"}))
    with pytest.raises(ExtractError):
        extract(archive_set(tmp_path / "evil.zip"))
    assert not (tmp_path.parent / "outside.txt").exists()
    assert not (tmp_path / "evil").exists(), "a failed extraction leaves nothing behind"


def test_tar_and_delete_after(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("notes.txt")
        info.size = 3
        archive.addfile(info, io.BytesIO(b"abc"))
    (tmp_path / "pack.tar.gz").write_bytes(buffer.getvalue())
    target = extract(archive_set(tmp_path / "pack.tar.gz"), delete_after=True)
    assert (target / "notes.txt").read_bytes() == b"abc"
    assert not (tmp_path / "pack.tar.gz").exists()


@pytest.mark.skipif(find_7zip() is None, reason="7-Zip is not installed")
def test_multi_part_7z_with_7zip(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "big.bin").write_bytes(payload(300_000))
    out = tmp_path / "out"
    out.mkdir()
    subprocess.run([find_7zip(), "a", "-v100k", "-mx0", str(out / "big.7z"), str(source / "big.bin")],
                   check=True, capture_output=True)
    parts = sorted(p.name for p in out.iterdir())
    assert parts[0] == "big.7z.001" and len(parts) >= 3
    target = extract(archive_set(out / parts[-1]))
    assert (target / "big.bin").read_bytes() == (source / "big.bin").read_bytes()


def test_without_7zip_rar_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("fetch_app.extract.find_7zip", lambda: None)
    (tmp_path / "a.rar").write_bytes(b"Rar!\x1a\x07\x00")
    with pytest.raises(ExtractError, match="7-Zip"):
        extract(archive_set(tmp_path / "a.rar"))


# -- end to end ---------------------------------------------------------------------


def folder(boot, admin):
    return boot.storage_root / "downloads" / str(admin.user["id"])


def wait_for(path, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def test_downloaded_zip_is_extracted(admin, fileserver, boot):
    fileserver.add("photos.zip", zip_bytes({"a.jpg": b"1", "b.jpg": b"2"}))
    job_id = admin.submit(fileserver.url("file", "photos.zip"))["created"][0]["id"]
    admin.wait_state(job_id, "completed")
    assert wait_for(folder(boot, admin) / "photos" / "b.jpg")
    assert (folder(boot, admin) / "photos.zip").exists(), "the archive is kept by default"
    actions = [item["action"] for item in admin.get("/activity").json()["items"]]
    assert "extract.completed" in actions


def test_auto_extract_can_be_turned_off(admin, fileserver, boot):
    admin.put("/settings", json={"downloads": {"auto_extract": False}}).raise_for_status()
    fileserver.add("keep.zip", zip_bytes({"a.txt": b"1"}))
    job_id = admin.submit(fileserver.url("file", "keep.zip"))["created"][0]["id"]
    admin.wait_state(job_id, "completed")
    time.sleep(0.5)
    assert not (folder(boot, admin) / "keep").exists()


@pytest.mark.skipif(find_7zip() is None, reason="7-Zip is not installed")
def test_multi_part_set_is_extracted_after_the_last_part(admin, fileserver, boot, tmp_path):
    admin.put("/settings", json={"downloads": {"delete_after_extract": True}}).raise_for_status()
    source = tmp_path / "src"
    source.mkdir()
    data = payload(250_000)
    (source / "film.bin").write_bytes(data)
    parts_dir = tmp_path / "parts"
    parts_dir.mkdir()
    subprocess.run([find_7zip(), "a", "-v100k", "-mx0", str(parts_dir / "film.7z"), str(source / "film.bin")],
                   check=True, capture_output=True)
    names = sorted(p.name for p in parts_dir.iterdir())
    for name in names:
        fileserver.add(name, (parts_dir / name).read_bytes())
    # The last part is slow, so the others finish first and must not start the extraction.
    created = admin.submit(*[fileserver.url("slow" if n == names[-1] else "file", n) for n in names])["created"]
    for job in created:
        admin.wait_state(job["id"], "completed", timeout=60)
    assert wait_for(folder(boot, admin) / "film" / "film.bin")
    assert (folder(boot, admin) / "film" / "film.bin").read_bytes() == data
    # The parts are deleted once the extraction has finished, a moment after film.bin appears.
    deadline = time.monotonic() + 10
    while any((folder(boot, admin) / n).exists() for n in names) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not any((folder(boot, admin) / n).exists() for n in names), "parts deleted after extracting"
