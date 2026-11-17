"""Authentication, sessions, invitations and password handling."""

from datetime import timedelta

from fetch_app.db import utcnow
from fetch_app.models import Session, User

from .conftest import ADMIN_PASSWORD, SETUP_TOKEN, USER_PASSWORD


def test_bootstrap_reports_setup_required(api):
    data = api.get("/bootstrap").json()
    assert data["setup_required"] is True
    assert data["invitations_required"] is True
    assert "setup_token" not in data


def test_setup_requires_the_setup_token(api):
    bad = api.post("/setup", json={"setup_token": "nope", "username": "admin", "password": ADMIN_PASSWORD})
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "bad_setup_token"
    assert api.get("/bootstrap").json()["setup_required"] is True


def test_setup_creates_admin_and_cannot_be_repeated(api):
    data = api.setup_admin()
    assert data["user"]["role"] == "admin"
    again = api.post("/setup", json={"setup_token": SETUP_TOKEN, "username": "other", "password": ADMIN_PASSWORD})
    assert again.status_code == 409
    assert api.get("/bootstrap").json()["setup_required"] is False


def test_setup_enforces_password_policy(api):
    response = api.post("/setup", json={"setup_token": SETUP_TOKEN, "username": "admin", "password": "short"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "weak_password"


def test_passwords_are_hashed_with_argon2id(admin, app):
    with app.state.ctx.db.session() as db:
        stored = db.query(User).one().password_hash
    assert stored.startswith("$argon2id$")
    assert ADMIN_PASSWORD not in stored


def test_login_logout_cycle(admin):
    assert admin.get("/auth/me").status_code == 200
    assert admin.post("/auth/logout").status_code == 200
    assert admin.get("/auth/me").status_code == 401
    admin.login("admin", ADMIN_PASSWORD)
    assert admin.get("/auth/me").json()["user"]["username"] == "admin"


def test_session_cookie_is_httponly_and_token_is_stored_hashed(api, app):
    response = api.post("/setup", json={"setup_token": SETUP_TOKEN, "username": "admin", "password": ADMIN_PASSWORD})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = api.client.cookies.get("fetch_session")
    with app.state.ctx.db.session() as db:
        assert db.query(Session).filter(Session.token_hash == token).count() == 0
        assert db.query(Session).count() == 1


def test_wrong_password_and_unknown_user_look_identical(admin):
    anonymous = admin.fork()
    wrong = anonymous.post("/auth/login", json={"username": "admin", "password": "wrong-password"})
    unknown = anonymous.post("/auth/login", json={"username": "ghost", "password": "wrong-password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_login_is_rate_limited(admin):
    anonymous = admin.fork()
    for _ in range(5):
        assert anonymous.post("/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    locked = anonymous.post("/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers


def test_expired_session_is_rejected(admin, app):
    with app.state.ctx.db.session() as db:
        db.query(Session).update({Session.expires_at: utcnow() - timedelta(seconds=1)})
    assert admin.get("/auth/me").status_code == 401


def test_state_changing_requests_need_the_csrf_token(admin):
    admin.csrf = ""
    response = admin.post("/auth/logout")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_cross_origin_requests_are_rejected(admin):
    response = admin.post("/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bad_origin"


def test_registration_requires_an_invitation(admin):
    anonymous = admin.fork()
    response = anonymous.post("/auth/register", json={"token": "", "username": "mallory", "password": USER_PASSWORD})
    assert response.status_code == 403
    response = anonymous.post("/auth/register", json={"token": "made-up", "username": "mallory", "password": USER_PASSWORD})
    assert response.status_code == 403


def test_invitation_flow_and_single_use(admin):
    token = admin.invite()
    friend = admin.fork()
    assert friend.get(f"/auth/invitation/{token}").json()["valid"] is True
    data = friend.register(token, "alice")
    assert data["user"]["role"] == "user"
    second = admin.fork()
    assert second.get(f"/auth/invitation/{token}").status_code == 404
    reuse = second.post("/auth/register", json={"token": token, "username": "bob", "password": USER_PASSWORD})
    assert reuse.status_code == 403


def test_invitation_token_is_not_stored_in_clear(admin, app):
    from fetch_app.models import Invitation

    token = admin.invite()
    with app.state.ctx.db.session() as db:
        assert db.query(Invitation).one().token_hash != token
    assert all("token" not in row for row in admin.get("/invitations").json())


def test_revoked_invitation_cannot_be_used(admin):
    token = admin.invite()
    invitation_id = admin.get("/invitations").json()[0]["id"]
    assert admin.delete(f"/invitations/{invitation_id}").status_code == 200
    response = admin.fork().post("/auth/register", json={"token": token, "username": "late", "password": USER_PASSWORD})
    assert response.status_code == 403


def test_duplicate_username_is_rejected(admin):
    response = admin.fork().post(
        "/auth/register", json={"token": admin.invite(), "username": "ADMIN", "password": USER_PASSWORD}
    )
    assert response.status_code == 409


def test_open_registration_when_invitations_are_not_required(admin):
    admin.put("/settings", json={"security": {"invitations_required": False}})
    data = admin.fork().register("", "walkin")
    assert data["user"]["role"] == "user"


def test_password_change_signs_out_other_sessions(user):
    other_browser = user.fork()
    other_browser.login("alice", USER_PASSWORD)
    wrong = user.post("/auth/password", json={"current_password": "nope", "new_password": "brand-new-pass-1"})
    assert wrong.status_code == 403
    ok = user.post("/auth/password", json={"current_password": USER_PASSWORD, "new_password": "brand-new-pass-1"})
    assert ok.json()["sessions_revoked"] == 1
    assert other_browser.get("/auth/me").status_code == 401
    assert user.get("/auth/me").status_code == 200
    user.fork().login("alice", "brand-new-pass-1")


def test_disabled_account_cannot_sign_in_and_loses_its_session(admin, user):
    assert admin.patch(f"/users/{user.user['id']}", json={"is_active": False}).status_code == 200
    assert user.get("/auth/me").status_code == 401
    response = user.fork().post("/auth/login", json={"username": "alice", "password": USER_PASSWORD})
    assert response.status_code == 401


def test_user_can_list_and_revoke_own_sessions(user):
    second = user.fork()
    second.login("alice", USER_PASSWORD)
    sessions = user.get("/auth/sessions").json()
    assert len(sessions) == 2
    target = next(s for s in sessions if not s["current"])
    assert user.delete(f"/auth/sessions/{target['id']}").status_code == 200
    assert second.get("/auth/me").status_code == 401
