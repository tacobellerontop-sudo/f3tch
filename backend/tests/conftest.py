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

BASE_URL = "http://127.0.0.1:8787"


def make_boot(tmp_path: Path, **overrides) -> BootConfig:
    values = dict(
        home=tmp_path,
        config_file=tmp_path / "config" / "fetch.toml",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        storage_root=tmp_path / "storage",
        frontend_dist=tmp_path / "dist",
        secret_key=Fernet.generate_key().decode(),
        ssrf_allowlist=["127.0.0.1"],
    )
    values.update(overrides)
    return BootConfig(**values)


class Api:
    """TestClient that talks to FETCH the way its own window does: from this computer."""

    def __init__(self, app, base_url: str = BASE_URL, client: tuple[str, int] = ("127.0.0.1", 50000)) -> None:
        self.client = TestClient(app, base_url=base_url, client=client)
        self.user: dict = {}

    def __enter__(self) -> "Api":
        self.client.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self.client.__exit__(*exc)

    def get(self, path, **kw):
        return self.client.get(f"/api{path}", **kw)

    def post(self, path, json=None, headers=None, **kw):
        return self.client.post(f"/api{path}", json=json, headers=headers, **kw)

    def put(self, path, json=None, **kw):
        return self.client.put(f"/api{path}", json=json, **kw)

    def patch(self, path, json=None, **kw):
        return self.client.patch(f"/api{path}", json=json, **kw)

    def delete(self, path, **kw):
        return self.client.delete(f"/api{path}", **kw)

    def open(self) -> dict:
        """What the dashboard does first; also notes the id of the local profile,
        which names the storage folder (``storage/downloads/<id>``)."""
        response = self.get("/bootstrap")
        assert response.status_code == 200, response.text
        self.user = {"id": self.client.app.state.ctx.local_user_id}
        return response.json()

    # There are no accounts: these names remain for the tests written when
    # there were, and simply open the dashboard.
    def setup_admin(self, *_args, **_kwargs) -> dict:
        return self.open()

    def login(self, *_args, **_kwargs) -> dict:
        return self.open()

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
    """The dashboard, opened. (The name dates from when there were accounts.)"""
    api.open()
    # Tests should not wait 30 s for automatic retries.
    api.put("/settings", json={"downloads": {"retry_delay_seconds": 1}})
    return api


def payload(size: int, seed: int = 1) -> bytes:
    return os.urandom(16) * (size // 16) + bytes([seed % 256]) * (size % 16)
