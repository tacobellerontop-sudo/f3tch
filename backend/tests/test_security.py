"""URL validation, SSRF protection, secrets handling and response hardening."""

import asyncio
import json
import sqlite3

import pytest

from fetch_app.logging_setup import redact
from fetch_app.security.urlguard import GuardedResolver, UnsafeUrlError, UrlGuard

from .conftest import make_boot, payload

BLOCKED = [
    "http://localhost/x",
    "http://LOCALHOST./x",
    "http://127.0.0.1/x",
    "http://127.1.2.3:8080/x",
    "http://10.0.0.5/x",
    "http://172.16.4.4/x",
    "http://192.168.1.1/router",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/tailscale",
    "http://0.0.0.0/x",
    "http://[::1]/x",
    "http://[fe80::1]/x",
    "http://[fd00::1]/x",
    "http://[::ffff:127.0.0.1]/x",
    "http://[::ffff:10.0.0.1]/x",
    "http://intranet/x",
    "http://printer.local/x",
    "http://nas.lan/x",
    "http://2130706433/x",
    "ftp://example.com/file",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "//example.com/x",
    "http:///nohost",
    "http://example.com:99999/x",
    "http://exa\nmple.com/x",
    "",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_guard_blocks_unsafe_urls(url):
    with pytest.raises(UnsafeUrlError):
        UrlGuard().check_url(url)


@pytest.mark.parametrize("url", ["https://example.com/a.zip", "http://8.8.8.8/x", "https://[2606:4700::1111]/x"])
def test_guard_allows_public_urls(url):
    assert UrlGuard().check_url(url) == url


def test_allowlist_is_explicit():
    guard = UrlGuard(["192.168.1.20", "10.1.0.0/16", "nas.lan"])
    assert guard.check_url("http://192.168.1.20/x")
    assert guard.check_url("http://10.1.2.3/x")
    assert guard.check_url("http://nas.lan/x")
    with pytest.raises(UnsafeUrlError):
        guard.check_url("http://192.168.1.21/x")


def test_resolver_drops_private_addresses(monkeypatch):
    """A public-looking name that resolves to a private address is refused."""

    async def fake(host, port=0, family=0):
        return [{"hostname": host, "host": "10.0.0.7", "port": port, "family": family, "proto": 0, "flags": 0}]

    async def scenario():
        resolver = GuardedResolver(UrlGuard())
        monkeypatch.setattr(resolver._inner, "resolve", fake)
        await resolver.resolve("rebind.example.com", 80)

    with pytest.raises(OSError):
        asyncio.run(scenario())


def test_submitting_private_urls_is_rejected(tmp_path):
    from fetch_app.app import create_app

    from .conftest import Api

    with Api(create_app(make_boot(tmp_path, ssrf_allowlist=[]))) as api:
        api.setup_admin()
        for url in ("http://127.0.0.1:9/x", "http://192.168.0.10/file.zip", "http://localhost/a"):
            result = api.submit(url)
            assert result["created"] == []
            assert result["rejected"][0]["code"] == "unsafe_url"
        inspected = api.post("/downloads/inspect", json={"urls": ["http://169.254.169.254/latest"]}).json()
        assert inspected["items"][0]["supported"] is False


def test_redirects_to_private_addresses_are_blocked(admin, fileserver):
    """The first hop is allowed (allowlisted test server); the redirect target is not."""
    result = admin.submit(fileserver.url("redirect-private", "x"))
    assert result["created"] == []
    assert result["rejected"][0]["code"] == "unsafe_url"


def test_redirect_to_allowed_target_is_followed(admin, fileserver):
    data = fileserver.add("via-redirect.bin", payload(4000))
    job_id = admin.submit(fileserver.url("redirect", "via-redirect.bin"))["created"][0]["id"]
    job = admin.wait_state(job_id, "completed", "failed")
    assert job["state"] == "completed" and job["total_bytes"] == len(data)


def test_provider_credentials_are_encrypted_at_rest_and_never_returned(admin, user, boot):
    secret = "pd-secret-api-key-123456"
    response = admin.put("/providers/pixeldrain/credentials", json={"values": {"api_key": secret}})
    assert response.status_code == 200
    assert secret not in response.text
    assert response.json()["credential_fields_set"] == ["api_key"]
    assert secret not in admin.get("/providers").text
    assert secret not in user.get("/providers").text
    assert "credential_fields_set" not in user.get("/providers").json()[0]
    raw = sqlite3.connect(boot.database_path).execute("select secret from provider_credentials").fetchone()[0]
    assert secret not in raw
    for path in boot.data_dir.glob("fetch.db*"):
        assert secret.encode() not in path.read_bytes()


def test_credentials_validation(admin):
    assert admin.put("/providers/direct/credentials", json={"values": {"x": "y"}}).status_code == 422
    assert admin.put("/providers/pixeldrain/credentials", json={"values": {"bogus": "y"}}).status_code == 422
    assert admin.put("/providers/rapidgator/credentials", json={"values": {"login": "a@b.c"}}).status_code == 422


def test_log_redaction():
    cleaned = redact({"password": "hunter2", "nested": {"api_key": "k", "ok": "v"}, "items": [{"token": "t"}]})
    assert cleaned == {"password": "***", "nested": {"api_key": "***", "ok": "v"}, "items": [{"token": "***"}]}
    assert redact("https://user:pw@example.com/a") == "https://***@example.com/a"


def test_secrets_never_reach_the_log_file(tmp_path, fileserver):
    from fetch_app.app import create_app
    from fetch_app.logging_setup import setup_logging

    from .conftest import ADMIN_PASSWORD, Api

    boot = make_boot(tmp_path)
    setup_logging(boot.log_dir, "DEBUG", console=False)
    with Api(create_app(boot)) as api:
        api.setup_admin()
        api.put("/providers/rapidgator/credentials",
                json={"values": {"login": "me@example.com", "password": "rg-very-secret"}})
        api.post("/auth/logout")
        api.fork().post("/auth/login", json={"username": "admin", "password": "wrong-secret-guess"})
        token = api.client.cookies.get("fetch_session") or ""
    text = (boot.log_dir / "fetch.log").read_text()
    assert text, "the log should not be empty"
    for secret in (ADMIN_PASSWORD, "rg-very-secret", "wrong-secret-guess", boot.secret_key, boot.setup_token):
        assert secret not in text
    assert not token or token not in text
    for line in text.splitlines():
        json.loads(line)  # structured: every line is a JSON document


def test_security_headers(admin):
    response = admin.get("/auth/me")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "access-control-allow-origin" not in response.headers


def test_no_cors_for_foreign_origins(admin):
    response = admin.client.options(
        "/api/auth/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in response.headers


def test_errors_do_not_leak_internals(admin, monkeypatch, app):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail /etc/fetch")

    monkeypatch.setattr(app.state.ctx.storage, "disk", boom)
    admin.client.app = app
    from fastapi.testclient import TestClient

    quiet = TestClient(app, raise_server_exceptions=False)
    quiet.portal = admin.client.portal
    quiet.cookies = admin.client.cookies
    response = quiet.get("/api/admin/health")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Something went wrong on the server."}}


def test_control_endpoint_needs_the_local_token(admin):
    assert admin.post("/control/shutdown").status_code == 404
    assert admin.post("/control/shutdown", headers={"X-Fetch-Control": "guess"}).status_code == 404


def test_setting_validation_rejects_wildcard_cors(admin):
    response = admin.put("/settings", json={"network": {"cors_origins": ["*"]}})
    assert response.status_code == 422
