"""Update checks against a local emulation of GitHub's releases API."""

import json

import pytest

from fetch_app import __version__
from fetch_app.updates import UpdateError, is_newer, parse_version

from .emulators import Emulator


class GitHubEmulator(Emulator):
    """Answers ``GET /repos/<owner>/<name>/releases/latest``."""

    def __init__(self) -> None:
        super().__init__()
        self.status = 200
        self.release = {
            "tag_name": "v99.1.0",
            "name": "FETCH 99.1.0",
            "body": "### Added\n- Something new",
            "published_at": "2026-11-01T10:00:00Z",
            "html_url": "https://github.com/example/fetch/releases/tag/v99.1.0",
            "assets": [{"name": "FETCH-99.1.0.zip"}, {"name": "FETCH-99.1.0.zip.sha256"}],
        }

    def handle(self, method, path, query, headers, body):
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


def test_updates_are_for_administrators_only(user, github):
    assert user.get("/admin/update").status_code == 403
    assert user.post("/admin/update/apply").status_code == 403
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
