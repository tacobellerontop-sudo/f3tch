"""Dropbox, MediaFire and WeTransfer adapters, against local stand-ins."""

import hashlib
import os

import pytest

from fetch_app.app import create_app
from fetch_app.models import FileRecord
from fetch_app.providers.direct import DirectHttpProvider
from fetch_app.providers.filehosts import DropboxProvider, MediaFireProvider, WeTransferProvider

from .conftest import Api, make_boot
from .emulators import MediaFireEmulator, WeTransferEmulator


def open_api(tmp_path, *providers):
    app = create_app(make_boot(tmp_path), providers=[*providers, DirectHttpProvider()])
    api = Api(app).__enter__()
    api.open()
    api.put("/settings", json={"downloads": {"retry_delay_seconds": 1}})
    return api


def saved(tmp_path, api, name):
    return tmp_path / "storage" / "downloads" / str(api.user["id"]) / name


# -- Dropbox -----------------------------------------------------------------------


def test_dropbox_share_link_gets_dl_1():
    url = "https://www.dropbox.com/scl/fi/abc123/movie.mkv?rlkey=xyz&dl=0"
    assert DropboxProvider.direct_url(url) == "https://www.dropbox.com/scl/fi/abc123/movie.mkv?rlkey=xyz&dl=1"
    direct = "https://dl.dropboxusercontent.com/s/abc/movie.mkv"
    assert DropboxProvider.direct_url(direct) == direct


class LocalDropbox(DropboxProvider):
    def can_handle(self, url):
        return "/cd/" in url or "/html/" in url


def test_dropbox_download(tmp_path, fileserver):
    fileserver.disposition = "report.pdf"
    data = fileserver.add("abc", os.urandom(3 * 1024 * 1024))
    fileserver.add("gone", b"<html>deleted</html>")
    api = open_api(tmp_path, LocalDropbox())
    try:
        items = api.post("/downloads/inspect", json={"urls": [
            fileserver.url("cd", "abc") + "?dl=0", fileserver.url("html", "gone"),
        ]}).json()["items"]
        assert items[0]["provider"] == "dropbox" and items[0]["filename"] == "report.pdf"
        assert items[0]["size"] == len(data)
        assert items[1]["supported"] is False and items[1]["restriction"] == "not_found"
        job = api.submit(fileserver.url("cd", "abc") + "?dl=0")["created"][0]
        done = api.wait_state(job["id"], "completed", timeout=60)
        assert saved(tmp_path, api, "report.pdf").read_bytes() == data
        assert done["engine"] == "segmented"
    finally:
        api.__exit__(None, None, None)


# -- MediaFire ---------------------------------------------------------------------


@pytest.fixture
def mediafire():
    emulator = MediaFireEmulator()
    yield emulator
    emulator.stop()


class LocalMediaFire(MediaFireProvider):
    """Points the real download host at the stand-in."""

    def can_handle(self, url):
        return "mediafire.com" in url

    @staticmethod
    def find_download_url(page):
        found = MediaFireProvider.find_download_url(page)
        return found and LocalMediaFire.emulator_base + "/dl/" + found.split("/")[4]


def test_mediafire_button_parsing():
    plain = '<a class="input" href="https://download2391.mediafire.com/a/k/f.zip" id="downloadButton">'
    assert MediaFireProvider.find_download_url(plain) == "https://download2391.mediafire.com/a/k/f.zip"
    import base64

    encoded = base64.b64encode(b"https://download1.mediafire.com/b/k/g.zip").decode()
    scrambled = f'<a href="javascript:void(0)" id="downloadButton" data-scrambled-url="{encoded}">'
    assert MediaFireProvider.find_download_url(scrambled) == "https://download1.mediafire.com/b/k/g.zip"
    assert MediaFireProvider.find_download_url("<html>nothing</html>") is None


@pytest.mark.parametrize("scrambled", [False, True])
def test_mediafire_download_with_checksum(tmp_path, mediafire, scrambled):
    mediafire.scrambled = scrambled
    data = os.urandom(2 * 1024 * 1024 + 5)
    mediafire.files["abc123def456"] = ("game.zip", data)
    LocalMediaFire.emulator_base = mediafire.base
    api = open_api(tmp_path, LocalMediaFire(site=mediafire.base))
    try:
        url = "https://www.mediafire.com/file/abc123def456/game.zip/file"
        item = api.post("/downloads/inspect", json={"urls": [url, "https://www.mediafire.com/file/zzzzzz999999/x/file",
                                                            "https://www.mediafire.com/folder/abc123/stuff"]}).json()["items"]
        assert item[0]["filename"] == "game.zip" and item[0]["size"] == len(data)
        assert item[1]["restriction"] == "not_found"
        assert item[2]["restriction"] == "unsupported_link"
        job = api.submit(url)["created"][0]
        done = api.wait_state(job["id"], "completed", timeout=60)
        assert saved(tmp_path, api, "game.zip").read_bytes() == data
        assert done["engine"] == "segmented"
        with api.client.app.state.ctx.db.session() as db:
            assert db.query(FileRecord).one().checksum == f"sha256:{hashlib.sha256(data).hexdigest()}"
    finally:
        api.__exit__(None, None, None)


def test_mediafire_password_is_reported(tmp_path, mediafire):
    mediafire.password = True
    mediafire.files["abc123def456"] = ("secret.zip", b"x")
    api = open_api(tmp_path, LocalMediaFire(site=mediafire.base))
    try:
        item = api.post("/downloads/inspect", json={"urls": ["https://www.mediafire.com/file/abc123def456/s/file"]}).json()["items"][0]
        assert item["supported"] is False and item["restriction"] == "auth_required"
    finally:
        api.__exit__(None, None, None)


# -- WeTransfer --------------------------------------------------------------------


@pytest.fixture
def wetransfer():
    emulator = WeTransferEmulator()
    yield emulator
    emulator.stop()


class LocalWeTransfer(WeTransferProvider):
    def can_handle(self, url):
        return url.startswith(self.site)


def test_wetransfer_short_link_download(tmp_path, wetransfer):
    wetransfer.data = os.urandom(500_000)
    api = open_api(tmp_path, LocalWeTransfer(site=wetransfer.base))
    try:
        url = f"{wetransfer.base}/t-short"
        item = api.post("/downloads/inspect", json={"urls": [url]}).json()["items"][0]
        assert item["provider"] == "wetransfer" and item["filename"] == "holiday.zip", item
        assert item["size"] == len(wetransfer.data)
        job = api.submit(url)["created"][0]
        api.wait_state(job["id"], "completed", timeout=60)
        assert saved(tmp_path, api, "holiday.zip").read_bytes() == wetransfer.data
    finally:
        api.__exit__(None, None, None)


def test_wetransfer_expired(tmp_path, wetransfer):
    wetransfer.expired = True
    api = open_api(tmp_path, LocalWeTransfer(site=wetransfer.base))
    try:
        item = api.post("/downloads/inspect", json={"urls": [f"{wetransfer.base}/t-short"]}).json()["items"][0]
        assert item["supported"] is False and item["restriction"] == "not_found"
    finally:
        api.__exit__(None, None, None)
