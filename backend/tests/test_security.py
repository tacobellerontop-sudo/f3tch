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


def test_provider_credentials_are_encrypted_at_rest_and_never_returned(admin, boot):
    secret = "pd-secret-api-key-123456"
    response = admin.put("/providers/pixeldrain/credentials", json={"values": {"api_key": secret}})
    assert response.status_code == 200
    assert secret not in response.text
    assert response.json()["credential_fields_set"] == ["api_key"]
    assert secret not in admin.get("/providers").text
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

    from .conftest import Api

    boot = make_boot(tmp_path)
    setup_logging(boot.log_dir, "DEBUG", console=False)
    with Api(create_app(boot)) as api:
        api.setup_admin()
        api.put("/providers/rapidgator/credentials",
                json={"values": {"login": "me@example.com", "password": "rg-very-secret"}})
        control_token = api.client.app.state.ctx.control_token
        api.post("/control/shutdown", headers={"X-Fetch-Control": "wrong-secret-guess"})
    text = (boot.log_dir / "fetch.log").read_text()
    assert text, "the log should not be empty"
    for secret in ("rg-very-secret", "wrong-secret-guess", boot.secret_key, control_token):
        assert secret not in text
    for line in text.splitlines():
        json.loads(line)  # structured: every line is a JSON document


def test_security_headers(admin):
    response = admin.get("/bootstrap")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "access-control-allow-origin" not in response.headers


def test_no_cors_for_foreign_origins(admin):
    response = admin.client.options(
        "/api/bootstrap", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in response.headers


def test_errors_do_not_leak_internals(admin, monkeypatch, app):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail /etc/fetch")

    monkeypatch.setattr(app.state.ctx.storage, "disk", boom)
    admin.client.app = app
    from fastapi.testclient import TestClient

    quiet = TestClient(app, raise_server_exceptions=False, base_url=str(admin.client.base_url),
                       client=("127.0.0.1", 50000))
    quiet.portal = admin.client.portal
    response = quiet.get("/api/admin/health")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Something went wrong on the server."}}


def test_control_endpoint_needs_the_local_token(admin):
    assert admin.post("/control/shutdown").status_code == 404
    assert admin.post("/control/shutdown", headers={"X-Fetch-Control": "guess"}).status_code == 404
    assert admin.post("/control/show", headers={"X-Fetch-Control": "guess"}).status_code == 404
    token = admin.client.app.state.ctx.control_token
    assert admin.post("/control/show", headers={"X-Fetch-Control": token}).json() == {"ok": True, "window": False}


# -- local only: there are no accounts, so only this computer may talk to FETCH ----


def test_there_is_no_sign_in(api):
    assert api.get("/downloads").status_code == 200
    for path in ("/auth/me", "/auth/login", "/setup", "/users", "/invitations", "/admin/sessions"):
        assert api.get(path).status_code in (404, 405), path
        assert api.post(path, json={}).status_code in (404, 405), path


def test_always_listens_on_this_computer_only(admin, boot):
    from fetch_app.config import load_config

    assert admin.get("/settings").json()["effective"]["bind_address"] == "127.0.0.1"
    # Neither the settings nor an old configuration file can expose FETCH.
    assert admin.put("/settings", json={"network": {"bind_address": "0.0.0.0"}}).status_code == 422
    boot.config_file.parent.mkdir(parents=True, exist_ok=True)
    boot.config_file.write_text('[server]\nbind_address = "0.0.0.0"\nport = 9000\n')
    loaded = load_config(boot.home, boot.config_file)
    assert loaded.port == 9000 and not hasattr(loaded, "bind_address")
    assert admin.client.app.state.ctx.settings.effective_bind()[0] == "127.0.0.1"


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8787", "192.168.1.20:8787", "fetch.local", ""])
def test_requests_under_a_foreign_host_name_are_refused(admin, host):
    """DNS rebinding: a web page elsewhere points its own name at 127.0.0.1."""
    response = admin.get("/downloads", headers={"Host": host})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "local_only"


@pytest.mark.parametrize("host", ["127.0.0.1:8787", "localhost:8787", "LOCALHOST", "[::1]:8787"])
def test_local_host_names_are_accepted(admin, host):
    assert admin.get("/downloads", headers={"Host": host}).status_code == 200


def test_requests_from_another_computer_are_refused(app):
    from .conftest import Api

    with Api(app, client=("192.168.1.50", 50000)) as remote:
        assert remote.get("/downloads").status_code == 403
        assert remote.get("/health").status_code == 403
        assert remote.client.get("/").status_code == 403


def test_other_web_pages_cannot_change_anything(admin, fileserver):
    """Cross-site requests carry the Origin of the page that sent them."""
    for origin in ("https://evil.example", "http://127.0.0.1:9999", "http://localhost:8787", "null"):
        headers = {"Origin": origin}
        assert admin.post("/downloads", json={"urls": ["https://example.com/a"]}, headers=headers).status_code == 403
        assert admin.client.put("/api/settings", json={}, headers=headers).status_code == 403
        assert admin.client.delete("/api/files", params={"path": "x"}, headers=headers).status_code == 403
    own = {"Origin": "http://127.0.0.1:8787"}
    assert admin.client.put("/api/settings", json={}, headers=own).status_code == 200


def test_old_databases_with_accounts_carry_over(tmp_path, fileserver):
    """A 1.x installation: its first administrator becomes the local profile."""
    import sqlite3

    from alembic import command
    from alembic.config import Config

    from fetch_app.app import create_app
    from fetch_app.runtime import resource_dir

    from .conftest import Api

    boot = make_boot(tmp_path)
    boot.ensure_dirs()
    cfg = Config(str(resource_dir() / "alembic.ini"))
    cfg.set_main_option("script_location", str(resource_dir() / "alembic"))
    cfg.set_main_option("sqlalchemy.url", boot.database_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "0001")
    db = sqlite3.connect(boot.database_path)
    now = "2026-11-16 10:00:00"
    for uid, name, role in ((1, "friend", "user"), (2, "boss", "admin")):
        db.execute(
            "insert into users (id, username, display_name, password_hash, role, is_active, theme,"
            " must_change_password, created_at) values (?, ?, ?, '$argon2id$secret', ?, 1, 'light', 0, ?)",
            (uid, name, name, role, now),
        )
    db.execute("insert into sessions (user_id, token_hash, csrf_token, created_at, last_seen_at, expires_at,"
               " ip_address, user_agent) values (2, 'h', 'c', ?, ?, ?, '', '')", (now, now, now))
    db.execute("insert into user_quotas values (1, 2, 0, 0, 0, 0, 100)")
    db.execute(
        "insert into downloads (user_id, url, provider, filename, state, priority, position, destination, folder,"
        " downloaded_bytes, speed_bps, avg_speed_bps, active_seconds, engine, attempts, max_attempts, error_code,"
        " error_message, wait_reason, final_path, created_at, updated_at) values (2, 'https://example.com/a.bin',"
        " 'direct', 'a.bin', 'completed', 1, 1, 'private', '', 5, 0, 0, 0, '', 1, 3, '', '', '', 'a.bin', ?, ?)",
        (now, now),
    )
    db.execute("insert into app_settings values ('security', '{\"session_hours\": 12}', ?)", (now,))
    db.execute("insert into app_settings values ('network', '{\"bind_address\": \"0.0.0.0\", \"port\": 9191,"
               " \"public_url\": \"https://fetch.example\"}', ?)", (now,))
    db.execute("insert into app_settings values ('downloads', '{\"max_per_user_concurrent\": 2,"
               " \"max_global_concurrent\": 5}', ?)", (now,))
    db.commit()
    db.close()
    (boot.storage_root / "downloads" / "2").mkdir(parents=True)
    (boot.storage_root / "downloads" / "2" / "a.bin").write_bytes(b"12345")

    with Api(create_app(boot)) as api:
        api.open()
        assert api.user["id"] == 2, "the administrator, not the lowest id"
        assert api.get("/bootstrap").json()["theme"] == "light"
        assert [j["filename"] for j in api.get("/downloads").json()["items"]] == ["a.bin"]
        assert [i["name"] for i in api.get("/files").json()["items"]] == ["a.bin"]
        data = api.get("/settings").json()
        assert data["settings"]["downloads"]["max_global_concurrent"] == 5
        assert data["settings"]["network"]["port"] == 9191
        assert data["effective"]["bind_address"] == "127.0.0.1"
        assert set(data["settings"]) == {"general", "downloads", "storage", "network"}
    db = sqlite3.connect(boot.database_path)
    tables = {row[0] for row in db.execute("select name from sqlite_master where type = 'table'")}
    assert not tables & {"sessions", "invitations", "user_quotas"}
    assert db.execute("select distinct password_hash from users").fetchall() == [("",)]
    db.close()
