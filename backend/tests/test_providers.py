"""Provider detection, capability reporting and adapter workflows."""

import json
import os
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fetch_app.app import create_app
from fetch_app.providers.direct import DirectHttpProvider
from fetch_app.providers.gdrive import GoogleDriveProvider
from fetch_app.providers.mega import MegaProvider
from fetch_app.providers.pixeldrain import PixeldrainProvider
from fetch_app.providers.rapidgator import RapidgatorProvider
from fetch_app.providers.registry import builtin_providers

from .conftest import Api, make_boot, payload
from .emulators import DriveEmulator, MegaEmulator, PixeldrainEmulator, RapidgatorEmulator

RG_FILE = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://rapidgator.net/file/0123456789abcdef0123456789abcdef/movie.mkv.html", "rapidgator"),
        ("https://rg.to/file/0123456789abcdef0123456789abcdef", "rapidgator"),
        ("https://mega.nz/file/AbCdEfGh#0123456789012345678901234567890123456789012", "mega"),
        ("https://mega.nz/#!AbCdEfGh!0123456789012345678901234567890123456789012", "mega"),
        ("https://mega.nz/folder/AbCdEfGh#key", "mega"),
        ("https://pixeldrain.com/u/abc12345", "pixeldrain"),
        ("https://pixeldrain.com/l/list1234", "pixeldrain"),
        ("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing", "gdrive"),
        ("https://drive.google.com/uc?export=download&id=1AbCdEfGhIjKlMnOp", "gdrive"),
        ("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit", "gdrive"),
        ("https://example.com/files/a.zip", "direct"),
        ("http://rapidgator.net.evil.example/file/x", "direct"),
        ("https://notmega.nz/file/x", "direct"),
    ],
)
def test_provider_detection(url, expected):
    providers = sorted(builtin_providers(), key=lambda p: p.priority)
    assert next(p.id for p in providers if p.can_handle(url)) == expected


def test_every_provider_declares_capabilities_and_verification():
    for provider in builtin_providers():
        described = provider.describe()
        assert described["id"] and described["name"] and described["verification"]
        assert set(described["capabilities"]) >= {"inspect", "download", "resume", "anonymous", "credentials"}
    by_id = {p.id: p for p in builtin_providers()}
    assert by_id["rapidgator"].capabilities.requires_credentials
    assert by_id["rapidgator"].capabilities.captcha
    assert by_id["gdrive"].capabilities.requires_credentials
    assert not by_id["mega"].capabilities.credentials
    assert not by_id["mega"].capabilities.folders
    assert "NOT verified against the live service" in by_id["rapidgator"].verification


def test_provider_list_and_toggle(admin, user, fileserver):
    listed = {p["id"]: p for p in admin.get("/providers").json()}
    assert set(listed) == {"rapidgator", "mega", "pixeldrain", "gdrive", "direct"}
    assert listed["direct"]["ready"] is True
    assert listed["rapidgator"]["ready"] is False and listed["gdrive"]["ready"] is False
    fileserver.add("a.bin", b"data")
    assert admin.patch("/providers/direct", json={"enabled": False}).json()["enabled"] is False
    result = user.submit(fileserver.url("file", "a.bin"))
    assert result["created"] == [] and result["rejected"][0]["code"] == "provider_disabled"
    admin.patch("/providers/direct", json={"enabled": True})
    assert len(user.submit(fileserver.url("file", "a.bin"))["created"]) == 1


# -- Rapidgator --------------------------------------------------------------


@pytest.fixture
def rapidgator(tmp_path):
    emulator = RapidgatorEmulator()
    app = create_app(make_boot(tmp_path), providers=[RapidgatorProvider(emulator.api_url), DirectHttpProvider()])
    with Api(app) as api:
        api.setup_admin()
        api.put("/settings", json={"downloads": {"retry_delay_seconds": 1}})
        yield api, emulator
    emulator.stop()


def rg_url(name="archive.rar"):
    return f"https://rapidgator.net/file/{RG_FILE}/{name}.html"


def test_rapidgator_without_account_reports_captcha_and_is_not_queued(rapidgator):
    api, emulator = rapidgator
    item = api.post("/downloads/inspect", json={"urls": [rg_url()]}).json()["items"][0]
    assert item["provider"] == "rapidgator" and item["supported"] is False
    assert item["restriction"] == "captcha_required" and "CAPTCHA" in item["message"]
    assert item["filename"] == "archive.rar"
    result = api.submit(rg_url())
    assert result["created"] == [] and result["rejected"][0]["code"] == "captcha_required"
    assert emulator.calls == [], "no request is made to Rapidgator without an account"
    report = api.post("/providers/rapidgator/test").json()
    assert report["ok"] is False and "CAPTCHA" in report["message"]


def test_rapidgator_premium_account_downloads_through_the_api(rapidgator, tmp_path):
    api, emulator = rapidgator
    data = payload(150_000)
    emulator.files[RG_FILE] = ("archive.rar", data)
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "premium-pass"}})
    report = api.post("/providers/rapidgator/test").json()
    assert report["ok"] is True and report["account"]["premium"] is True

    item = api.post("/downloads/inspect", json={"urls": [rg_url()]}).json()["items"][0]
    assert item["supported"] and item["filename"] == "archive.rar" and item["size"] == len(data)
    job = api.wait_state(api.submit(rg_url())["created"][0]["id"], "completed", "failed")
    assert job["state"] == "completed", job
    stored = tmp_path / "storage" / "downloads" / str(api.user["id"]) / "archive.rar"
    assert stored.read_bytes() == data
    # The session token is reused rather than logging in for every call.
    logins = [c for c in emulator.calls if c[1].endswith("/user/login")]
    assert len(logins) <= 2


def test_rapidgator_expired_session_logs_in_again(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "premium-pass"}})
    assert api.post("/downloads/inspect", json={"urls": [rg_url()]}).json()["items"][0]["supported"]
    emulator.tokens.clear()  # the provider forgot our session
    assert api.post("/downloads/inspect", json={"urls": [rg_url()]}).json()["items"][0]["supported"]


def test_rapidgator_wrong_password(rapidgator):
    api, _ = rapidgator
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "wrong"}})
    report = api.post("/providers/rapidgator/test").json()
    assert report["ok"] is False
    item = api.post("/downloads/inspect", json={"urls": [rg_url()]}).json()["items"][0]
    assert item["supported"] is False and item["restriction"] == "auth_failed"
    listed = {p["id"]: p for p in api.get("/providers").json()}
    assert listed["rapidgator"]["health"] == "error"
    errors = api.get("/providers/rapidgator/errors").json()
    assert errors and errors[0]["action"] == "provider.test"


def test_rapidgator_free_account_is_reported_not_bypassed(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "free@example.com", "password": "free-pass"}})
    report = api.post("/providers/rapidgator/test").json()
    assert report["ok"] is True and report["account"]["premium"] is False
    assert "not a Premium account" in report["message"]
    job = api.wait_state(api.submit(rg_url())["created"][0]["id"], "failed")
    assert job["error_code"] == "captcha_required"
    assert not any(c[1].startswith("/dl/") for c in emulator.calls), "no download was attempted"
    errors = api.get("/providers/rapidgator/errors").json()
    assert any(e["code"] == "captcha_required" for e in errors)


def test_rapidgator_delay_is_waited_out(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    emulator.delay = 2
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "premium-pass"}})
    job_id = api.submit(rg_url())["created"][0]["id"]
    waiting = api.wait_state(job_id, "waiting")
    assert waiting["error_code"] == "wait_required" and "waiting period" in waiting["wait_reason"]
    assert not any(c[1].startswith("/dl/") for c in emulator.calls), "nothing is downloaded during the wait"
    started = time.monotonic()
    emulator.delay = 0
    api.wait_state(job_id, "completed", timeout=15)
    assert time.monotonic() - started >= 1.0


def test_rapidgator_quota_is_reported(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    emulator.traffic_exhausted = True
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "premium-pass"}})
    job = api.wait_state(api.submit(rg_url())["created"][0]["id"], "failed")
    assert job["error_code"] == "quota_exceeded"


def test_rapidgator_folder_links_are_unsupported(rapidgator):
    api, _ = rapidgator
    item = api.post("/downloads/inspect", json={"urls": ["https://rapidgator.net/folder/123/Stuff.html"]}).json()["items"][0]
    assert item["supported"] is False and item["restriction"] == "unsupported_link"


# -- MEGA ----------------------------------------------------------------------


@pytest.fixture
def mega(tmp_path):
    emulator = MegaEmulator()
    app = create_app(make_boot(tmp_path), providers=[MegaProvider(emulator.api_url), DirectHttpProvider()])
    with Api(app) as api:
        api.setup_admin()
        api.put("/settings", json={"downloads": {"retry_delay_seconds": 1, "retry_count": 0}})
        yield api, emulator
    emulator.stop()


def test_mega_public_file_is_decrypted_and_verified(mega, tmp_path):
    api, emulator = mega
    # 600 kB spans MEGA's 128 kB / 256 kB / 384 kB MAC chunks and ends off a block boundary.
    plain = os.urandom(600_003)
    link = emulator.publish("AbCdEfGh", "holiday photos.zip", plain)
    item = api.post("/downloads/inspect", json={"urls": [link]}).json()["items"][0]
    assert item == item | {"provider": "mega", "supported": True, "filename": "holiday photos.zip", "size": len(plain)}
    job = api.wait_state(api.submit(link)["created"][0]["id"], "completed", "failed")
    assert job["state"] == "completed", job
    stored = tmp_path / "storage" / "downloads" / str(api.user["id"]) / "holiday photos.zip"
    assert stored.read_bytes() == plain


def test_mega_corrupted_content_fails_verification(mega):
    api, emulator = mega
    link = emulator.publish("AbCdEfGh", "x.bin", os.urandom(200_000))
    emulator.corrupt = True
    job = api.wait_state(api.submit(link)["created"][0]["id"], "failed")
    assert job["error_code"] == "verification_failed"
    assert api.get("/files").json()["items"] == []


def test_mega_wrong_key_missing_file_and_folder(mega):
    api, emulator = mega
    link = emulator.publish("AbCdEfGh", "x.bin", b"hello")
    wrong_key = link.split("#")[0] + "#" + "A" * 43
    items = api.post("/downloads/inspect", json={"urls": [
        wrong_key, "https://mega.nz/file/Missing1#" + "A" * 43, "https://mega.nz/folder/AbCdEfGh#key",
        "https://mega.nz/file/AbCdEfGh",
    ]}).json()["items"]
    assert [i["restriction"] for i in items] == ["unsupported_link", "not_found", "unsupported_link", "unsupported_link"]
    assert all(not i["supported"] for i in items)


def test_mega_transfer_quota_waits(mega):
    api, emulator = mega
    link = emulator.publish("AbCdEfGh", "x.bin", os.urandom(1000))
    emulator.quota_seconds = 120
    job = api.wait_state(api.submit(link)["created"][0]["id"], "waiting")
    assert job["error_code"] == "wait_required" and "quota" in job["wait_reason"]


def test_mega_chunk_boundaries():
    from fetch_app.providers.mega import chunk_boundaries

    sizes = [length for _, length in chunk_boundaries(0x20000 * 36 + 0x100000 * 2 + 5)]
    assert sizes[:8] == [0x20000 * i for i in range(1, 9)]
    assert sizes[8:] == [0x100000, 0x100000, 5]


# -- Pixeldrain ----------------------------------------------------------------


@pytest.fixture
def pixeldrain(tmp_path):
    emulator = PixeldrainEmulator()
    app = create_app(make_boot(tmp_path), providers=[PixeldrainProvider(emulator.api_url), DirectHttpProvider()])
    with Api(app) as api:
        api.setup_admin()
        yield api, emulator
    emulator.stop()


def test_pixeldrain_download_with_checksum(pixeldrain, tmp_path):
    api, emulator = pixeldrain
    data = payload(90_000)
    emulator.files["abc12345"] = ("clip.mp4", data)
    job = api.wait_state(api.submit("https://pixeldrain.com/u/abc12345")["created"][0]["id"], "completed", "failed")
    assert job["state"] == "completed" and job["filename"] == "clip.mp4"
    assert (tmp_path / "storage" / "downloads" / str(api.user["id"]) / "clip.mp4").read_bytes() == data
    with api.client.app.state.ctx.db.session() as db:
        from fetch_app.models import FileRecord

        assert db.query(FileRecord).one().checksum.startswith("sha256:")


def test_pixeldrain_captcha_list_and_missing(pixeldrain):
    api, emulator = pixeldrain
    emulator.files["abc12345"] = ("clip.mp4", b"x")
    emulator.captcha = True
    items = api.post("/downloads/inspect", json={"urls": [
        "https://pixeldrain.com/u/abc12345", "https://pixeldrain.com/l/list1234", "https://pixeldrain.com/u/missing00",
    ]}).json()["items"]
    assert [i["restriction"] for i in items] == ["captcha_required", "unsupported_link", "not_found"]


def test_pixeldrain_api_key_is_sent_and_tested(pixeldrain):
    api, emulator = pixeldrain
    assert api.post("/providers/pixeldrain/test").json()["ok"] is True  # anonymous reachability
    api.put("/providers/pixeldrain/credentials", json={"values": {"api_key": "wrong"}})
    assert api.post("/providers/pixeldrain/test").json()["ok"] is False
    api.put("/providers/pixeldrain/credentials", json={"values": {"api_key": "pd-key"}})
    report = api.post("/providers/pixeldrain/test").json()
    assert report["ok"] is True and report["account"]["username"] == "tester"
    assert api.delete("/providers/pixeldrain/credentials").json()["configured"] is False


# -- Google Drive --------------------------------------------------------------


@pytest.fixture
def drive(tmp_path):
    emulator = DriveEmulator()
    app = create_app(
        make_boot(tmp_path),
        providers=[GoogleDriveProvider(emulator.api_url, emulator.token_url), DirectHttpProvider()],
    )
    with Api(app) as api:
        api.setup_admin()
        yield api, emulator
    emulator.stop()


DRIVE_URL = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing"


def test_drive_requires_credentials(drive):
    api, emulator = drive
    item = api.post("/downloads/inspect", json={"urls": [DRIVE_URL]}).json()["items"][0]
    assert item["supported"] is False and item["restriction"] == "auth_required"
    assert emulator.calls == []
    assert api.post("/providers/gdrive/test").json()["ok"] is False


def test_drive_download_with_api_key(drive, tmp_path):
    api, emulator = drive
    data = payload(70_000)
    emulator.add("1AbCdEfGhIjKlMnOp", "backup.zip", data)
    api.put("/providers/gdrive/credentials", json={"values": {"api_key": "drive-key"}})
    assert api.post("/providers/gdrive/test").json()["ok"] is True
    job = api.wait_state(api.submit(DRIVE_URL)["created"][0]["id"], "completed", "failed")
    assert job["state"] == "completed", job
    assert (tmp_path / "storage" / "downloads" / str(api.user["id"]) / "backup.zip").read_bytes() == data
    # The key travels in a header, never in a URL that could end up in logs.
    assert all("key" not in query for _, _, query, _, _ in emulator.calls)


def test_drive_rejected_key_native_docs_folders_and_quota(drive):
    api, emulator = drive
    emulator.add("1AbCdEfGhIjKlMnOp", "backup.zip", b"data")
    emulator.add("2NativeDocument00", "Notes", b"", mime="application/vnd.google-apps.document")
    api.put("/providers/gdrive/credentials", json={"values": {"api_key": "bad"}})
    assert api.post("/providers/gdrive/test").json()["ok"] is False
    api.put("/providers/gdrive/credentials", json={"values": {"api_key": "drive-key"}})
    items = api.post("/downloads/inspect", json={"urls": [
        "https://drive.google.com/file/d/2NativeDocument00/view",
        "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp",
        "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/edit",
        "https://drive.google.com/file/d/3DoesNotExist0000/view",
    ]}).json()["items"]
    assert [i["restriction"] for i in items] == ["unsupported_link", "unsupported_link", "unsupported_link", "not_found"]
    emulator.quota_exceeded = True
    job = api.wait_state(api.submit(DRIVE_URL)["created"][0]["id"], "failed")
    assert job["error_code"] == "quota_exceeded"


def test_drive_service_account_signs_a_valid_jwt(drive):
    api, emulator = drive
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    emulator.public_key = private.public_key()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    key_json = json.dumps({"client_email": "svc@project.iam.gserviceaccount.com", "private_key": pem})
    api.put("/providers/gdrive/credentials", json={"values": {"service_account_json": key_json}})
    report = api.post("/providers/gdrive/test").json()
    assert report["ok"] is True and report["account"]["email"].startswith("svc@")
    assert emulator.claims["iss"] == "svc@project.iam.gserviceaccount.com"
    assert emulator.claims["scope"].endswith("drive.readonly")
    emulator.add("1AbCdEfGhIjKlMnOp", "backup.zip", b"data")
    assert api.post("/downloads/inspect", json={"urls": [DRIVE_URL]}).json()["items"][0]["supported"]
    api.put("/providers/gdrive/credentials", json={"values": {"service_account_json": "{not json"}})
    assert api.post("/providers/gdrive/test").json()["ok"] is False
