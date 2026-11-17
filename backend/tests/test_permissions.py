"""Role-based access control and cross-user isolation of downloads."""

import pytest

from .conftest import payload

ADMIN_ONLY = [
    ("get", "/users"),
    ("get", "/invitations"),
    ("post", "/invitations"),
    ("get", "/settings"),
    ("put", "/settings"),
    ("get", "/admin/health"),
    ("get", "/admin/logs"),
    ("get", "/admin/sessions"),
    ("post", "/admin/workers/restart"),
    ("post", "/admin/cleanup"),
    ("patch", "/providers/direct"),
    ("put", "/providers/pixeldrain/credentials"),
    ("post", "/providers/direct/test"),
    ("get", "/providers/direct/errors"),
    ("get", "/users/1/quota"),
    ("put", "/users/1/quota"),
    ("delete", "/users/1"),
    ("post", "/users/1/reset-password"),
]


@pytest.mark.parametrize("method,path", ADMIN_ONLY)
def test_regular_users_cannot_reach_admin_endpoints(user, method, path):
    kwargs = {"json": {}} if method in ("post", "put", "patch") else {}
    response = getattr(user, method)(path, **kwargs)
    assert response.status_code == 403, f"{method} {path} -> {response.status_code}"
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("path", ["/auth/me", "/downloads", "/files", "/providers", "/activity", "/users"])
def test_anonymous_requests_are_rejected(api, path):
    assert api.get(path).status_code == 401


def test_users_cannot_see_or_control_each_others_downloads(admin, user, fileserver):
    fileserver.add("private.bin", payload(1000))
    job_id = admin.submit(fileserver.url("file", "private.bin"))["created"][0]["id"]
    admin.wait_state(job_id, "completed")

    assert user.get(f"/downloads/{job_id}").status_code == 404
    assert user.post(f"/downloads/{job_id}/retry").status_code == 404
    assert user.delete(f"/downloads/{job_id}").status_code == 404
    assert user.patch(f"/downloads/{job_id}", json={"priority": 2}).status_code == 404
    assert user.get("/downloads").json()["items"] == []
    bulk = user.post("/downloads/bulk", json={"ids": [job_id], "action": "remove"}).json()
    assert bulk == {"done": [], "skipped": [job_id]}
    assert user.post("/downloads/reorder", json={"ids": [job_id]}).status_code == 404
    assert admin.get(f"/downloads/{job_id}").status_code == 200


def test_admin_sees_all_downloads_with_owner(admin, user, fileserver):
    fileserver.add("a.bin", payload(500))
    job_id = user.submit(fileserver.url("file", "a.bin"))["created"][0]["id"]
    user.wait_state(job_id, "completed")
    items = admin.get("/downloads").json()["items"]
    assert [(i["id"], i["owner"]) for i in items] == [(job_id, "alice")]
    assert admin.get("/downloads", params={"owner": "me"}).json()["items"] == []
    assert admin.post(f"/downloads/{job_id}/retry").status_code == 409  # allowed, wrong state


def test_users_only_see_their_own_stats_and_activity(admin, user, fileserver):
    fileserver.add("a.bin", payload(500))
    admin.wait_state(admin.submit(fileserver.url("file", "a.bin"))["created"][0]["id"], "completed")
    assert user.get("/downloads/stats", params={"scope": "all"}).json()["completed"] == 0
    assert admin.get("/downloads/stats", params={"scope": "all"}).json()["completed"] == 1
    messages = [i["message"] for i in user.get("/activity", params={"scope": "all"}).json()["items"]]
    assert messages and not any("a.bin" in m for m in messages)
    assert all(i["ip_address"] == "" for i in user.get("/activity").json()["items"])


def test_urgent_priority_is_reserved_for_admins(user, fileserver):
    fileserver.add("a.bin", payload(100))
    created = user.submit(fileserver.url("file", "a.bin"), priority=3)["created"][0]
    assert created["priority"] == 2
    assert user.patch(f"/downloads/{created['id']}", json={"priority": 3}).status_code == 403


def test_last_admin_cannot_be_removed_or_demoted(admin):
    me = admin.user["id"]
    assert admin.patch(f"/users/{me}", json={"role": "user"}).status_code == 409
    assert admin.patch(f"/users/{me}", json={"is_active": False}).status_code == 409
    assert admin.delete(f"/users/{me}").status_code == 409
