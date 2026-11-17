"""Update checks against a local emulation of GitHub's releases API."""

import hashlib
import json
import types

import pytest

from fetch_app import __version__
from fetch_app.updates import UpdateError, is_newer, parse_version

from .emulators import Emulator


class GitHubEmulator(Emulator):
    """Answers ``GET /repos/<owner>/<name>/releases/latest``."""

    def __init__(self) -> None:
        super().__init__()
        self.status = 200
        self.files: dict[str, bytes] = {}
        self.release = {
            "tag_name": "v99.1.0",
            "name": "FETCH 99.1.0",
            "body": "### Added\n- Something new",
            "published_at": "2026-11-01T10:00:00Z",
            "html_url": "https://github.com/example/fetch/releases/tag/v99.1.0",
            "assets": [{"name": "FETCH-99.1.0.zip"}, {"name": "FETCH-99.1.0.zip.sha256"}],
        }

    def handle(self, method, path, query, headers, body):
        if method == "GET" and path.startswith("/download/") and path[10:] in self.files:
            return 200, {"Content-Type": "application/octet-stream"}, self.files[path[10:]]
        if method == "GET" and path == "/repos/example/fetch/releases/latest":
            if self.status != 200:
                return self.status, {}, {"message": "Not Found"}
            return 200, {}, self.release
        return 404, {}, {"message": "Not Found"}


@pytest.fixture
def github(admin):
    emulator = GitHubEmulator()
    boot = admin.client.app.state.ctx.boot
    boot.update_repository = "example/fetch"
    boot.update_api_url = emulator.base
    yield emulator
    emulator.stop()


def test_version_comparison():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("2.0") == (2, 0, 0)
    assert parse_version("release-1") is None
    assert is_newer("v1.0.1", "1.0.0")
    assert is_newer("1.10.0", "1.9.9")
    assert not is_newer("v1.0.0", "1.0.0")
    assert not is_newer("0.9.0", "1.0.0")
    assert not is_newer("nonsense", "1.0.0")


def test_reports_a_newer_release(admin, github):
    data = admin.get("/admin/update").json()
    assert data["current_version"] == __version__
    assert data["latest_version"] == "99.1.0"
    assert data["update_available"] is True
    assert data["package_available"] is True
    assert data["notes"].startswith("### Added")
    assert data["release_url"].startswith("https://github.com/")
    assert data["error"] == ""
    assert data["repository"] == "example/fetch"
    # Only the public release endpoint is called, anonymously.
    method, path, _query, headers, _body = github.calls[-1]
    assert (method, path) == ("GET", "/repos/example/fetch/releases/latest")
    assert "Authorization" not in headers and "Cookie" not in headers


def test_result_is_cached_until_refresh(admin, github):
    admin.get("/admin/update")
    admin.get("/admin/update")
    assert len(github.calls) == 1
    admin.get("/admin/update", params={"refresh": "true"})
    assert len(github.calls) == 2


def test_no_update_when_the_release_is_not_newer(admin, github):
    github.release["tag_name"] = f"v{__version__}"
    data = admin.get("/admin/update").json()
    assert data["update_available"] is False
    assert data["latest_version"] == __version__


def test_missing_release_and_bad_data_are_explained(admin, github):
    github.status = 404
    data = admin.get("/admin/update").json()
    assert data["update_available"] is False and data["latest_version"] is None
    assert "No published release" in data["error"]

    github.status = 200
    github.release["tag_name"] = "nightly"
    data = admin.get("/admin/update", params={"refresh": "true"}).json()
    assert "version number" in data["error"]

    # A release page that is not on github.com is never linked.
    github.release.update(tag_name="v99.0.0", html_url="https://evil.example/x")
    data = admin.get("/admin/update", params={"refresh": "true"}).json()
    assert data["update_available"] is True and data["release_url"] == ""


def test_automatic_check_can_be_switched_off(admin, github):
    assert admin.put("/settings", json={"general": {"check_for_updates": False}}).status_code == 200
    data = admin.get("/admin/update").json()
    assert data["auto_check"] is False and data["checked_at"] is None
    assert github.calls == []
    # An explicit check still works.
    assert admin.get("/admin/update", params={"refresh": "true"}).json()["update_available"] is True


def test_no_repository_means_no_request(admin, github):
    admin.client.app.state.ctx.boot.update_repository = ""
    data = admin.get("/admin/update", params={"refresh": "true"}).json()
    assert "switched off" in data["error"]
    assert github.calls == []


def test_apply_is_refused_when_the_server_cannot_restart_itself(admin, github):
    data = admin.get("/admin/update").json()
    assert data["can_apply"] is False and data["apply_blocker"]
    response = admin.post("/admin/update/apply")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "update_unavailable"


def test_apply_starts_the_updater_script_once(admin, github, monkeypatch, tmp_path):
    ctx = admin.client.app.state.ctx
    service = ctx.updates
    (ctx.boot.home / "update.ps1").write_text("# stub", encoding="utf-8")
    launched = []

    class FakePopen:
        def __init__(self, command, **kwargs):
            launched.append((command, kwargs))

    monkeypatch.setattr("fetch_app.updates.os.name", "nt")
    monkeypatch.setattr("fetch_app.updates.subprocess.Popen", FakePopen)
    monkeypatch.setattr(service, "_powershell", lambda: "powershell.exe")

    service.start(managed=True)
    command, kwargs = launched[0]
    assert command[0] == "powershell.exe"
    assert command[-3:] == [str(ctx.boot.home / "update.ps1"), "-Yes", "-Unattended"]
    assert kwargs["cwd"] == str(ctx.boot.home)
    status = json.loads(ctx.boot.update_status_file.read_text(encoding="utf-8"))
    assert status["state"] == "running" and status["from_version"] == __version__
    assert service.in_progress()

    with pytest.raises(UpdateError, match="already running"):
        service.start(managed=True)
    assert len(launched) == 1


def test_last_run_is_reported_and_stale_runs_expire(admin, github):
    boot = admin.client.app.state.ctx.boot
    boot.update_status_file.write_text(
        "﻿" + json.dumps({  # Windows PowerShell writes a BOM
            "state": "failed", "message": "The checksum did not match.",
            "from_version": "1.0.0", "to_version": "1.1.0",
            "started_at": "2026-11-01T10:00:00Z", "finished_at": "2026-11-01T10:01:00Z",
        }),
        encoding="utf-8",
    )
    data = admin.get("/admin/update").json()
    assert data["last_run"]["state"] == "failed"
    assert data["last_run"]["message"] == "The checksum did not match."
    assert data["in_progress"] is False

    boot.update_status_file.write_text(
        json.dumps({"state": "running", "started_at": "2020-01-01T00:00:00Z"}), encoding="utf-8"
    )
    assert admin.get("/admin/update").json()["in_progress"] is False


# -- the single-file executable ------------------------------------------------------


@pytest.fixture
def exe(admin, github, monkeypatch, tmp_path):
    """Make the server believe it is FETCH.exe in ``tmp_path`` with a newer release out."""
    path = tmp_path / "FETCH.exe"
    path.write_bytes(b"old executable")
    monkeypatch.setattr("fetch_app.updates.executable_path", lambda: path)
    payload = b"new executable " * 50_000
    github.files = {
        "FETCH.exe": payload,
        "FETCH.exe.sha256": f"{hashlib.sha256(payload).hexdigest()}  FETCH.exe\n".encode(),
    }
    github.release["assets"] = [
        {"name": name, "browser_download_url": f"{github.base}/download/{name}"} for name in github.files
    ]
    ctx = admin.client.app.state.ctx
    launched = []
    monkeypatch.setattr(ctx.updates, "_launch", lambda target: launched.append(target))
    server = types.SimpleNamespace(should_exit=False)
    monkeypatch.setattr(ctx, "server", server)
    return types.SimpleNamespace(path=path, payload=payload, launched=launched, server=server, ctx=ctx)


def test_executable_reports_its_own_kind_of_package(admin, github, exe):
    data = admin.get("/admin/update").json()
    assert data["source"] == "exe"
    assert data["package_available"] is True and data["can_apply"] is True

    github.release["assets"] = [{"name": "FETCH-99.1.0.zip"}]
    data = admin.get("/admin/update", params={"refresh": "true"}).json()
    assert data["package_available"] is False


def test_executable_replaces_itself_after_verifying_the_checksum(admin, github, exe):
    response = admin.post("/admin/update/apply")
    assert response.status_code == 200, response.text
    assert exe.path.read_bytes() == exe.payload
    assert exe.path.with_name("FETCH.exe.old").read_bytes() == b"old executable"
    assert not exe.path.with_name("FETCH.exe.new").exists()
    assert exe.launched == [exe.path]
    assert exe.server.should_exit is True
    status = json.loads(exe.ctx.boot.update_status_file.read_text(encoding="utf-8"))
    assert status["state"] == "running" and status["to_version"] == "99.1.0"


def test_executable_with_a_wrong_checksum_is_refused(admin, github, exe):
    github.files["FETCH.exe.sha256"] = b"0" * 64 + b"  FETCH.exe\n"
    response = admin.post("/admin/update/apply")
    assert response.status_code == 409
    assert "checksum" in response.json()["error"]["message"]
    assert exe.path.read_bytes() == b"old executable"
    assert not exe.path.with_name("FETCH.exe.new").exists()
    assert exe.launched == [] and exe.server.should_exit is False
    data = admin.get("/admin/update").json()
    assert data["last_run"]["state"] == "failed" and data["in_progress"] is False


def test_executable_is_only_downloaded_from_the_release_host(admin, github, exe):
    github.release["assets"][0]["browser_download_url"] = "https://downloads.example/FETCH.exe"
    github.release["assets"][1]["browser_download_url"] = "https://downloads.example/FETCH.exe.sha256"
    response = admin.post("/admin/update/apply")
    assert response.status_code == 409
    assert "unexpected download address" in response.json()["error"]["message"]
    assert exe.path.read_bytes() == b"old executable"


def test_executable_goes_back_when_the_new_one_cannot_start(admin, github, exe, monkeypatch):
    def fail(target):
        raise OSError("blocked")

    monkeypatch.setattr(exe.ctx.updates, "_launch", fail)
    response = admin.post("/admin/update/apply")
    assert response.status_code == 409
    assert exe.path.read_bytes() == b"old executable"
    assert exe.server.should_exit is False


def test_executable_is_not_replaced_without_a_newer_release(admin, github, exe):
    github.release["tag_name"] = f"v{__version__}"
    response = admin.post("/admin/update/apply")
    assert response.status_code == 409
    assert exe.path.read_bytes() == b"old executable"
