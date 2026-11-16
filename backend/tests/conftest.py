from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from fetch_app.app import create_app
from fetch_app.config import BootConfig

from .fileserver import FileServer

SETUP_TOKEN = "test-setup-token"
ADMIN_PASSWORD = "admin-password-1"
USER_PASSWORD = "user-password-1"


def make_boot(tmp_path: Path, **overrides) -> BootConfig:
    values = dict(
        home=tmp_path,
        config_file=tmp_path / "config" / "fetch.toml",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        storage_root=tmp_path / "storage",
        frontend_dist=tmp_path / "dist",
        secret_key=Fernet.generate_key().decode(),
        setup_token=SETUP_TOKEN,
        ssrf_allowlist=["127.0.0.1"],
    )
    values.update(overrides)
    return BootConfig(**values)


class Api:
    """TestClient wrapper that remembers the CSRF token like the real frontend."""

    def __init__(self, app, base_url: str = "http://testserver") -> None:
        self.client = TestClient(app, base_url=base_url)
        self.csrf = ""
        self.user: dict = {}

    def __enter__(self) -> "Api":
        self.client.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self.client.__exit__(*exc)

    def fork(self) -> "Api":
        """A second browser talking to the same running app."""
        other = Api.__new__(Api)
        other.client = TestClient(self.client.app, base_url=str(self.client.base_url))
        other.client.portal = self.client.portal  # share the running event loop
        other.csrf, other.user = "", {}
        return other

    def _headers(self, extra=None) -> dict:
        headers = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        headers.update(extra or {})
        return headers

    def get(self, path, **kw):
        return self.client.get(f"/api{path}", **kw)

    def post(self, path, json=None, headers=None, **kw):
        return self.client.post(f"/api{path}", json=json, headers=self._headers(headers), **kw)

    def put(self, path, json=None, **kw):
        return self.client.put(f"/api{path}", json=json, headers=self._headers(), **kw)

    def patch(self, path, json=None, **kw):
        return self.client.patch(f"/api{path}", json=json, headers=self._headers(), **kw)

    def delete(self, path, **kw):
        return self.client.delete(f"/api{path}", headers=self._headers(), **kw)

    def _remember(self, response) -> dict:
        assert response.status_code == 200, response.text
        data = response.json()
        self.csrf, self.user = data["csrf_token"], data["user"]
        return data

    def setup_admin(self, username="admin", password=ADMIN_PASSWORD) -> dict:
        return self._remember(
            self.post("/setup", json={"setup_token": SETUP_TOKEN, "username": username, "password": password})
        )

    def login(self, username, password) -> dict:
        self.csrf = ""
        return self._remember(self.post("/auth/login", json={"username": username, "password": password}))

    def invite(self, role="user") -> str:
        response = self.post("/invitations", json={"role": role})
        assert response.status_code == 201, response.text
        return response.json()["token"]

    def register(self, token, username, password=USER_PASSWORD) -> dict:
        return self._remember(
            self.post("/auth/register", json={"token": token, "username": username, "password": password})
        )

    # -- download helpers ------------------------------------------------------

    def submit(self, *urls, **extra) -> dict:
        response = self.post("/downloads", json={"urls": list(urls), **extra})
        assert response.status_code == 201, response.text
        return response.json()

    def download(self, job_id: int) -> dict:
        response = self.get(f"/downloads/{job_id}")
        assert response.status_code == 200, response.text
        return response.json()

    def wait_state(self, job_id: int, *states: str, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        job: dict = {}
        while time.monotonic() < deadline:
            job = self.download(job_id)
            if job["state"] in states:
                return job
            time.sleep(0.05)
        raise AssertionError(f"download {job_id} stuck in {job.get('state')} ({job.get('error_message')})")


@pytest.fixture(autouse=True)
def _no_aria2(request, monkeypatch):
    """aria2 is only started for tests marked ``aria2`` (it slows teardown)."""
    if "aria2" not in request.keywords:
        monkeypatch.setattr("fetch_app.app.find_aria2", lambda *a, **k: None)


@pytest.fixture
def fileserver():
    server = FileServer().start()
    yield server
    server.stop()


@pytest.fixture
def boot(tmp_path):
    return make_boot(tmp_path)


@pytest.fixture
def app(boot):
    return create_app(boot)


@pytest.fixture
def api(app):
    with Api(app) as client:
        yield client


@pytest.fixture
def admin(api):
    api.setup_admin()
    # Tests should not wait 30 s for automatic retries.
    api.put("/settings", json={"downloads": {"retry_delay_seconds": 1}})
    return api


@pytest.fixture
def user(admin):
    """A regular user in a separate browser session, next to ``admin``."""
    token = admin.invite()
    other = admin.fork()
    other.register(token, "alice")
    return other


def payload(size: int, seed: int = 1) -> bytes:
    return os.urandom(16) * (size // 16) + bytes([seed % 256]) * (size % 16)
