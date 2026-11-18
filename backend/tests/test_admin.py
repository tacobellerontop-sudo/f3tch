"""Settings, diagnostics and the activity log."""

from fetch_app.app import create_app

from .conftest import Api, make_boot, payload


def test_settings_are_validated(admin):
    cases = [
        {"downloads": {"max_global_concurrent": 0}},
        {"downloads": {"retry_count": 999}},
        {"network": {"port": 80}},
        {"network": {"bind_address": "0.0.0.0"}},
        {"network": {"cors_origins": ["*"]}},
        {"security": {"password_min_length": 12}},
        {"quota_defaults": {"max_queued": 5}},
        {"downloads": {"speed_limit_kbps": -1}},
        {"general": {"app_name": ""}},
        {"general": {"theme": "neon"}},
        {"general": {"unknown_field": 1}},
        {"nonexistent": {"x": 1}},
        {"storage": {"storage_root": "bad|path"}},
    ]
    for patch in cases:
        response = admin.put("/settings", json=patch)
        assert response.status_code == 422, patch
        assert response.json()["error"]["code"] == "invalid_settings"
    assert admin.get("/settings").json()["settings"]["downloads"]["max_global_concurrent"] == 3


def test_settings_persist_across_restart_and_flag_restart(tmp_path):
    boot = make_boot(tmp_path)
    with Api(create_app(boot)) as api:
        api.open()
        result = api.put("/settings", json={
            "general": {"app_name": "Friends DL", "theme": "light"},
            "downloads": {"max_global_concurrent": 7, "retry_count": 5, "speed_limit_kbps": 2048},
            "network": {"port": 9090},
        }).json()
        assert sorted(result["changed"]) == [
            "downloads.max_global_concurrent", "downloads.retry_count", "downloads.speed_limit_kbps",
            "general.app_name", "general.theme", "network.port",
        ]
        assert result["restart_required"] == ["network.port"]
        assert api.get("/bootstrap").json()["app_name"] == "Friends DL"
    with Api(create_app(boot)) as api:
        data = api.get("/settings").json()
        assert data["settings"]["general"]["app_name"] == "Friends DL"
        assert data["settings"]["downloads"] == data["settings"]["downloads"] | {"max_global_concurrent": 7, "retry_count": 5}
        assert data["effective"]["port"] == 9090
        assert data["restart_required"] == []


def test_history_files_and_credentials_survive_restart(tmp_path, fileserver):
    boot = make_boot(tmp_path)
    fileserver.add("a.bin", payload(100))
    with Api(create_app(boot)) as api:
        api.open()
        job_id = api.submit(fileserver.url("file", "a.bin"))["created"][0]["id"]
        api.wait_state(job_id, "completed")
        api.put("/providers/pixeldrain/credentials", json={"values": {"api_key": "k"}})
        api.patch("/preferences", json={"theme": "light"})
        profile = api.user["id"]
    with Api(create_app(boot)) as api:
        assert api.open()["theme"] == "light"
        assert api.user["id"] == profile, "the same local profile, not a new one"
        history = api.get("/downloads").json()["items"]
        assert [(j["id"], j["state"]) for j in history] == [(job_id, "completed")]
        assert [i["name"] for i in api.get("/files").json()["items"]] == ["a.bin"]
        providers = {p["id"]: p for p in api.get("/providers").json()}
        assert providers["pixeldrain"]["configured"] is True


def test_bootstrap_reports_usage(admin, fileserver):
    fileserver.add("a.bin", payload(4000))
    admin.wait_state(admin.submit(fileserver.url("file", "a.bin"))["created"][0]["id"], "completed")
    admin.client.app.state.ctx.storage.invalidate_usage()
    data = admin.get("/bootstrap").json()
    assert data["app_name"] == "FETCH" and data["version"]
    assert data["usage"] == {
        "storage_bytes": 4000, "storage_limit_bytes": 0, "max_concurrent": 3,
        "active_downloads": 0, "open_downloads": 0, "downloads_today": 1,
    }
    assert "setup_required" not in data and "csrf_token" not in data
    assert admin.patch("/preferences", json={"theme": "neon"}).status_code == 422


def test_health_logs_and_cleanup(tmp_path, fileserver):
    from fetch_app.logging_setup import setup_logging

    boot = make_boot(tmp_path)
    setup_logging(boot.log_dir, "INFO", console=False)
    with Api(create_app(boot)) as api:
        api.open()
        health = api.get("/admin/health").json()
        names = {c["name"]: c for c in health["checks"]}
        assert names["Database"]["ok"] and names["Download manager"]["ok"] and names["Storage"]["ok"]
        assert names["aria2 engine"]["optional"] is True
        assert health["manager"]["running"] is True and health["disk"]["total"] > 0

        logs = api.get("/admin/logs", params={"limit": 50}).json()["items"]
        assert logs and {"ts", "level", "logger", "message"} <= set(logs[0])
        assert any("started" in entry["message"] for entry in logs)
        assert api.get("/admin/logs", params={"level": "ERROR"}).json()["items"] == []

        orphan = boot.storage_root / "temporary" / "424242"
        orphan.mkdir(parents=True)
        (orphan / "payload").write_bytes(b"x" * 10)
        assert api.post("/admin/cleanup").json()["temp_folders"] == 1
        assert not orphan.exists()


def test_activity_log_records_changes(admin, fileserver):
    fileserver.add("a.bin", payload(100))
    admin.wait_state(admin.submit(fileserver.url("file", "a.bin"))["created"][0]["id"], "completed")
    entries = admin.get("/activity", params={"category": "admin"}).json()["items"]
    assert any(e["action"] == "settings.updated" for e in entries)
    assert all("username" not in e and "ip_address" not in e for e in entries)
    assert admin.get("/activity", params={"category": "download"}).json()["total"] >= 2
    assert admin.get("/activity", params={"q": "a.bin"}).json()["total"] >= 1


def test_spa_fallback_and_static_assets(tmp_path):
    boot = make_boot(tmp_path)
    with Api(create_app(boot)) as api:
        assert api.client.get("/").status_code == 503  # not built yet: clear message
        (boot.frontend_dist / "assets").mkdir(parents=True)
        (boot.frontend_dist / "index.html").write_text("<html>FETCH</html>")
        (boot.frontend_dist / "assets" / "app.js").write_text("console.log(1)")
        assert "FETCH" in api.client.get("/").text
        assert "FETCH" in api.client.get("/downloads/42").text, "client-side routes get the app shell"
        assert api.client.get("/assets/app.js").headers["cache-control"].startswith("public")
        assert api.client.get("/assets/missing.js").status_code == 404
        assert api.client.get("/api/nope").json()["error"]["code"] == "not_found"
        assert api.client.get("/..%2f..%2fconfig%2ffetch.toml").text == "<html>FETCH</html>"


def test_activity_entries_can_be_deleted_one_by_one_or_cleared(admin, fileserver):
    fileserver.add("b.bin", payload(100))
    admin.wait_state(admin.submit(fileserver.url("file", "b.bin"))["created"][0]["id"], "completed")
    before = admin.get("/activity").json()["total"]
    assert before >= 3

    first = admin.get("/activity").json()["items"][0]
    assert admin.delete(f"/activity/{first['id']}").json() == {"deleted": 1}
    assert admin.get("/activity").json()["total"] == before - 1
    assert admin.delete(f"/activity/{first['id']}").status_code == 404

    downloads = admin.get("/activity", params={"category": "download"}).json()["total"]
    assert downloads >= 1
    result = admin.delete("/activity", params={"category": "download"}).json()
    assert result == {"deleted": downloads}
    assert admin.get("/activity", params={"category": "download"}).json()["total"] == 0
    remaining = admin.get("/activity").json()["total"]
    assert remaining == before - 1 - downloads and remaining > 0, "other categories stay"

    assert admin.delete("/activity").json() == {"deleted": remaining}
    assert admin.get("/activity").json()["total"] == 0
    assert admin.get("/downloads").json()["total"] == 1, "downloads are not touched"
