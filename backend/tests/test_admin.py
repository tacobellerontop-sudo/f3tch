"""Administration: users, quotas, sessions, settings, diagnostics."""

import time

from fetch_app.app import create_app

from .conftest import ADMIN_PASSWORD, USER_PASSWORD, Api, make_boot, payload


def test_user_listing_includes_usage(admin, user, fileserver):
    fileserver.add("a.bin", payload(4000))
    user.wait_state(user.submit(fileserver.url("file", "a.bin"))["created"][0]["id"], "completed")
    rows = {u["username"]: u for u in admin.get("/users").json()}
    assert set(rows) == {"admin", "alice"}
    assert rows["alice"]["storage_bytes"] == 4000 and rows["alice"]["total_downloads"] == 1
    assert rows["alice"]["active_sessions"] == 1 and rows["alice"]["quota"]["max_queued"] == 100
    assert rows["admin"]["quota"]["max_storage_bytes"] == 0
    assert "password_hash" not in rows["alice"]


def test_promote_and_demote(admin, user):
    uid = user.user["id"]
    assert admin.patch(f"/users/{uid}", json={"role": "admin"}).json()["role"] == "admin"
    assert user.get("/users").status_code == 200
    assert admin.patch(f"/users/{uid}", json={"role": "user"}).json()["role"] == "user"
    assert user.get("/users").status_code == 403


def test_disabling_a_user_requeues_their_running_jobs(admin, user, fileserver):
    fileserver.add("long.bin", payload(900_000))
    job_id = user.submit(fileserver.url("slow", "long.bin"))["created"][0]["id"]
    user.wait_state(job_id, "downloading")
    admin.patch(f"/users/{user.user['id']}", json={"is_active": False})
    time.sleep(0.3)
    assert admin.download(job_id)["state"] == "queued", "held while the account is disabled"
    admin.patch(f"/users/{user.user['id']}", json={"is_active": True})
    admin.wait_state(job_id, "completed", timeout=30)


def test_reset_password_generates_a_one_time_password(admin, user):
    response = admin.post(f"/users/{user.user['id']}/reset-password", json={}).json()
    temporary = response["temporary_password"]
    assert temporary and len(temporary) >= 16
    assert user.get("/auth/me").status_code == 401, "existing sessions are revoked"
    fresh = admin.fork()
    assert fresh.post("/auth/login", json={"username": "alice", "password": USER_PASSWORD}).status_code == 401
    assert fresh.login("alice", temporary)["user"]["must_change_password"] is True
    weak = admin.post(f"/users/{user.user['id']}/reset-password", json={"new_password": "short"})
    assert weak.status_code == 422


def test_delete_user_removes_account_jobs_and_optionally_files(admin, user, fileserver, boot):
    fileserver.add("a.bin", payload(100))
    user.wait_state(user.submit(fileserver.url("file", "a.bin"))["created"][0]["id"], "completed")
    uid = user.user["id"]
    folder = boot.storage_root / "downloads" / str(uid)
    assert (folder / "a.bin").exists()
    assert admin.delete(f"/users/{uid}", params={"delete_files": "true"}).json()["files_deleted"] is True
    assert not folder.exists()
    assert [u["username"] for u in admin.get("/users").json()] == ["admin"]
    assert admin.get("/downloads").json()["total"] == 0
    assert user.get("/auth/me").status_code == 401


def test_admin_sessions(admin, user):
    sessions = admin.get("/admin/sessions").json()
    assert {s["username"] for s in sessions} == {"admin", "alice"}
    mine = next(s for s in sessions if s["current"])
    assert mine["username"] == "admin"
    assert admin.delete(f"/admin/sessions/{mine['id']}").status_code == 400
    assert all("token" not in key for s in sessions for key in s)


def test_settings_are_validated(admin):
    cases = [
        {"downloads": {"max_global_concurrent": 0}},
        {"downloads": {"retry_count": 999}},
        {"network": {"port": 80}},
        {"network": {"bind_address": "not-an-ip"}},
        {"network": {"trusted_proxies": ["nonsense"]}},
        {"security": {"password_min_length": 3}},
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
        api.setup_admin()
        result = api.put("/settings", json={
            "general": {"app_name": "Friends DL", "theme": "light"},
            "downloads": {"max_global_concurrent": 7, "retry_count": 5},
            "network": {"port": 9090},
            "security": {"session_hours": 12},
        }).json()
        assert sorted(result["changed"]) == [
            "downloads.max_global_concurrent", "downloads.retry_count", "general.app_name",
            "general.theme", "network.port", "security.session_hours",
        ]
        assert result["restart_required"] == ["network.port"]
        assert api.get("/bootstrap").json()["app_name"] == "Friends DL"
    with Api(create_app(boot)) as api:
        api.login("admin", ADMIN_PASSWORD)
        data = api.get("/settings").json()
        assert data["settings"]["general"]["app_name"] == "Friends DL"
        assert data["settings"]["downloads"] == data["settings"]["downloads"] | {"max_global_concurrent": 7, "retry_count": 5}
        assert data["effective"]["port"] == 9090
        assert data["restart_required"] == []


def test_users_history_and_queue_survive_restart(tmp_path, fileserver):
    boot = make_boot(tmp_path)
    fileserver.add("a.bin", payload(100))
    with Api(create_app(boot)) as api:
        api.setup_admin()
        friend = api.fork()
        friend.register(api.invite(), "alice")
        job_id = friend.submit(fileserver.url("file", "a.bin"))["created"][0]["id"]
        friend.wait_state(job_id, "completed")
        api.put("/providers/pixeldrain/credentials", json={"values": {"api_key": "k"}})
    with Api(create_app(boot)) as api:
        api.login("alice", USER_PASSWORD)
        history = api.get("/downloads").json()["items"]
        assert [(j["id"], j["state"]) for j in history] == [(job_id, "completed")]
        assert [i["name"] for i in api.get("/files").json()["items"]] == ["a.bin"]
        api.login("admin", ADMIN_PASSWORD)
        providers = {p["id"]: p for p in api.get("/providers").json()}
        assert providers["pixeldrain"]["configured"] is True


def test_health_logs_and_cleanup(tmp_path, fileserver):
    from fetch_app.logging_setup import setup_logging

    boot = make_boot(tmp_path)
    setup_logging(boot.log_dir, "INFO", console=False)
    with Api(create_app(boot)) as api:
        api.setup_admin()
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


def test_activity_log_records_admin_actions(admin, user):
    admin.patch(f"/users/{user.user['id']}", json={"display_name": "Alice A."})
    entries = admin.get("/activity", params={"scope": "all", "category": "admin"}).json()["items"]
    assert any(e["action"] == "user.updated" for e in entries)
    assert all(e["ip_address"] for e in entries)
    logins = admin.get("/activity", params={"scope": "all", "category": "auth"}).json()
    assert logins["total"] >= 2
    assert admin.get("/activity", params={"scope": "all", "q": "Alice"}).json()["total"] >= 1


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
