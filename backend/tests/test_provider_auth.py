"""Authentication modes and Rapidgator's anonymous free download.

Everything here runs against the local emulation in ``emulators.py``. The
emulator refuses any step of the free flow that arrives early or out of order,
so these tests also prove that FETCH waits the announced period and that a
CAPTCHA answer only ever comes from the person, never from FETCH.
"""

import json
import time

import pytest

from fetch_app.app import create_app
from fetch_app.db import run_migrations
from fetch_app.providers import rapidgator as rapidgator_module
from fetch_app.providers.auth import choose_mode
from fetch_app.providers.base import AuthMode, ErrorCode, ProviderError
from fetch_app.providers.direct import DirectHttpProvider
from fetch_app.providers.gdrive import GoogleDriveProvider
from fetch_app.providers.rapidgator import RapidgatorProvider
from fetch_app.providers.registry import builtin_providers

from .conftest import Api, make_boot, payload
from .emulators import RapidgatorEmulator

RG_FILE = "0123456789abcdef0123456789abcdef"
RG_OTHER = "fedcba9876543210fedcba9876543210"


def rg_url(file_id=RG_FILE, name="archive.rar"):
    return f"https://rapidgator.net/file/{file_id}/{name}.html"


@pytest.fixture
def rapidgator(tmp_path):
    emulator = RapidgatorEmulator()
    app = create_app(
        make_boot(tmp_path),
        providers=[RapidgatorProvider(emulator.api_url, emulator.base), GoogleDriveProvider(), DirectHttpProvider()],
    )
    with Api(app) as api:
        api.setup_admin()
        api.put("/settings", json={"downloads": {"retry_delay_seconds": 1, "retry_count": 0}})
        yield api, emulator
    emulator.stop()


def inspect(api, url):
    return api.post("/downloads/inspect", json={"urls": [url]}).json()["items"][0]


def provider(api, provider_id="rapidgator"):
    return next(p for p in api.get("/providers").json() if p["id"] == provider_id)


def wait_captcha(api, job_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    job = {}
    while time.monotonic() < deadline:
        job = api.download(job_id)
        if job["state"] == "waiting" and job["error_code"] == "captcha_required":
            return job
        assert job["state"] not in ("failed", "completed"), job
        time.sleep(0.05)
    raise AssertionError(f"no CAPTCHA was requested: {job}")


# -- capability detection ----------------------------------------------------------


def test_every_provider_declares_its_authentication_modes():
    modes = {p.id: set(p.supported_auth_modes()) for p in builtin_providers()}
    assert modes == {
        "rapidgator": {AuthMode.ANONYMOUS, AuthMode.ACCOUNT},
        "pixeldrain": {AuthMode.ANONYMOUS, AuthMode.ACCOUNT},
        "mega": {AuthMode.ANONYMOUS},
        "direct": {AuthMode.ANONYMOUS},
        "gdrive": {AuthMode.ACCOUNT},
    }
    by_id = {p.id: p for p in builtin_providers()}
    assert by_id["rapidgator"].anonymous_available() and not by_id["rapidgator"].authentication_required()
    assert by_id["gdrive"].authentication_required() and not by_id["gdrive"].anonymous_available()
    assert not by_id["mega"].accepts_accounts() and by_id["pixeldrain"].accepts_accounts()
    for adapter in by_id.values():
        assert adapter.describe()["auth_modes"] == [m.value for m in adapter.auth_modes]


def test_mode_selection_rules():
    rg, drive, direct = RapidgatorProvider(), GoogleDriveProvider(), DirectHttpProvider()
    assert choose_mode(rg, "auto", account_configured=False) is AuthMode.ANONYMOUS
    assert choose_mode(rg, "auto", account_configured=True) is AuthMode.ACCOUNT
    assert choose_mode(rg, "anonymous", account_configured=True) is AuthMode.ANONYMOUS
    assert choose_mode(drive, "auto", account_configured=True) is AuthMode.ACCOUNT
    cases = [
        (drive, "auto", ErrorCode.AUTH_REQUIRED),
        (drive, "anonymous", ErrorCode.ANON_UNAVAILABLE),
        (rg, "account", ErrorCode.AUTH_REQUIRED),
        (direct, "account", ErrorCode.AUTH_REQUIRED),
    ]
    for adapter, preference, code in cases:
        with pytest.raises(ProviderError) as caught:
            choose_mode(adapter, preference, account_configured=False)
        assert caught.value.code == code


def test_provider_settings_report_authentication_state(rapidgator):
    api, emulator = rapidgator
    rg, drive = provider(api)["auth"], provider(api, "gdrive")["auth"]
    assert rg == rg | {
        "modes": ["anonymous", "account"], "anonymous_available": True, "authentication_required": False,
        "account_configured": False, "preference": "auto", "effective_mode": "anonymous", "status": "anonymous",
    }
    assert drive == drive | {
        "modes": ["account"], "anonymous_available": False, "authentication_required": True,
        "effective_mode": None, "status": "auth_required", "problem_code": "auth_required",
    }
    assert provider(api)["ready"] is True and provider(api, "gdrive")["ready"] is False

    # An account is stored: "auto" now uses it, and it is reported as unchecked.
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "premium-pass"}})
    rg = provider(api)["auth"]
    assert rg["account_configured"] and rg["status"] == "account" and rg["account_valid"] is None
    assert api.post("/providers/rapidgator/test").json()["ok"] is True
    assert provider(api)["auth"]["account_valid"] is True

    # The mode can be chosen explicitly, but only from the supported ones.
    assert api.put("/providers/rapidgator/auth-mode", json={"mode": "anonymous"}).json()["auth"]["status"] == "anonymous"
    assert api.put("/providers/gdrive/auth-mode", json={"mode": "anonymous"}).status_code == 422
    assert api.put("/providers/direct/auth-mode", json={"mode": "account"}).status_code == 422
    assert api.put("/providers/rapidgator/auth-mode", json={"mode": "bogus"}).status_code == 422

    # Choosing "account" with none stored is reported as exactly that.
    api.delete("/providers/rapidgator/credentials")
    api.put("/providers/rapidgator/auth-mode", json={"mode": "account"})
    assert provider(api)["auth"]["status"] == "auth_required"
    item = inspect(api, rg_url())
    assert item["supported"] is False and item["restriction"] == "auth_required"
    assert "none is stored" in item["message"]

    # A disabled provider is "unavailable", whatever its accounts.
    api.put("/providers/rapidgator/auth-mode", json={"mode": "auto"})
    api.patch("/providers/rapidgator", json={"enabled": False})
    assert provider(api)["auth"]["status"] == "unavailable"


def test_credentials_are_never_returned(rapidgator, user):
    api, _ = rapidgator
    secret = "premium-pass"
    api.put("/providers/rapidgator/credentials", json={"values": {"login": "premium@example.com", "password": secret}})
    api.post("/providers/rapidgator/test")
    for body in (api.get("/providers").text, api.get("/providers/rapidgator/errors").text):
        assert secret not in body and "premium@example.com" not in body


@pytest.fixture
def user(rapidgator):
    api, _ = rapidgator
    other = api.fork()
    other.register(api.invite(), "alice")
    return other


def test_other_users_see_state_but_cannot_change_accounts(rapidgator, user):
    api, _ = rapidgator
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "premium-pass"}})
    seen = provider(user)
    assert seen["auth"]["account_configured"] is True and seen["auth"]["status"] == "account"
    assert "credential_fields_set" not in seen and "premium" not in json.dumps(seen["auth"])
    assert user.put("/providers/rapidgator/auth-mode", json={"mode": "anonymous"}).status_code == 403
    assert user.put("/providers/rapidgator/credentials", json={"values": {"login": "a", "password": "b"}}).status_code == 403


# -- anonymous inspection and eligibility --------------------------------------------


def test_anonymous_inspection_reads_public_metadata(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("holiday.zip", payload(4096))
    item = inspect(api, rg_url(name="whatever"))
    assert item == item | {
        "provider": "rapidgator", "supported": True, "filename": "holiday.zip", "size": 4096,
        "premium_only": False, "auth_mode": "anonymous", "restriction": "",
    }
    assert "CAPTCHA" in item["message"]
    paths = [c[1] for c in emulator.calls]
    assert paths == [f"/file/{RG_FILE}"], "inspection reads the public page and nothing else"
    assert not any("/api/" in p for p in paths), "no account call is made"


def test_anonymous_eligibility_premium_only_deleted_and_blocked(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("big.iso", payload(1000))
    emulator.files[RG_OTHER] = ("ok.bin", payload(1000))
    emulator.premium_only.add(RG_FILE)
    emulator.display_sizes[RG_FILE] = "4.7 GB"
    premium, fine, gone = (inspect(api, u) for u in (
        rg_url(), rg_url(RG_OTHER), rg_url("00000000000000000000000000000000")))
    assert premium == premium | {"supported": False, "restriction": "premium_only", "premium_only": True,
                                 "filename": "big.iso", "size": int(4.7 * 1024**3)}
    assert fine["supported"] is True and fine["premium_only"] is False
    assert gone["supported"] is False and gone["restriction"] == "not_found"

    result = api.submit(rg_url(), rg_url(RG_OTHER), rg_url("00000000000000000000000000000000"))
    assert [r["code"] for r in result["rejected"]] == ["premium_only", "not_found"]
    assert len(result["created"]) == 1
    api.post(f"/downloads/{result['created'][0]['id']}/cancel")

    emulator.blocked = True
    blocked = inspect(api, rg_url(RG_OTHER))
    assert blocked["supported"] is False and blocked["restriction"] == "anonymous_unavailable"
    emulator.blocked, emulator.site_down = False, True
    down = inspect(api, rg_url(RG_OTHER))
    assert down["restriction"] == "provider_unavailable" and down["supported"] is True  # temporary
    report = api.post("/providers/rapidgator/test").json()
    assert report["ok"] is False


def test_link_requires_premium_interface(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("big.iso", b"x")
    emulator.files[RG_OTHER] = ("ok.bin", b"x")
    emulator.premium_only.add(RG_FILE)
    ctx = api.client.app.state.ctx
    adapter = ctx.registry.get("rapidgator")
    context = ctx.registry.context(adapter)
    assert context.auth_mode == "anonymous" and context.credentials is None
    call = api.client.portal.call  # run on the application's own event loop
    assert call(adapter.link_requires_premium, rg_url(), context) is True
    assert call(adapter.link_requires_premium, rg_url(RG_OTHER), context) is False


# -- the free download: wait, CAPTCHA by the user, transfer --------------------------


def test_free_download_waits_then_asks_the_user_for_the_captcha(rapidgator, tmp_path):
    api, emulator = rapidgator
    data = payload(120_000)
    emulator.files[RG_FILE] = ("archive.rar", data)
    emulator.free_seconds = 2
    job_id = api.submit(rg_url())["created"][0]["id"]

    # 1. Rapidgator's waiting period: reported, and waited out in full.
    waiting = api.wait_state(job_id, "waiting")
    assert waiting["error_code"] == "wait_required" and "waiting period" in waiting["wait_reason"]
    assert api.get(f"/downloads/{job_id}/captcha").status_code == 404

    # 2. The CAPTCHA is reported and handed to the user; FETCH posts nothing itself.
    job = wait_captcha(api, job_id)
    assert job["action_required"] == "captcha" and "CAPTCHA" in job["wait_reason"]
    assert emulator.early_requests == 0, "the timer was never cut short"
    challenge = api.get(f"/downloads/{job_id}/captcha").json()
    assert challenge == challenge | {"kind": "image", "has_image": True, "attempt": 1, "provider": "rapidgator"}
    image = api.get(f"/downloads/{job_id}/captcha/image")
    assert image.content == emulator.PNG and image.headers["content-type"] == "image/png"
    time.sleep(0.5)
    assert emulator.captcha_posts == [], "FETCH never answers a CAPTCHA on its own"
    assert emulator.free_transfers == 0

    # 3. A wrong answer from the user gets a fresh challenge, not a bypass.
    assert api.post(f"/downloads/{job_id}/captcha", json={}).status_code == 422
    assert api.post(f"/downloads/{job_id}/captcha", json={"answer": "nope"}).status_code == 200
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and len(emulator.captcha_posts) < 1:
        time.sleep(0.05)
    wait_captcha(api, job_id)
    again = api.get(f"/downloads/{job_id}/captcha").json()
    assert again["attempt"] == 2 and emulator.free_transfers == 0

    # 4. The user's correct answer is submitted unchanged and the file arrives.
    api.post(f"/downloads/{job_id}/captcha", json={"answer": emulator.captcha_answer})
    done = api.wait_state(job_id, "completed", "failed")
    assert done["state"] == "completed", done
    assert [p["DownloadCaptchaForm[verifyCode]"] for p in emulator.captcha_posts] == ["nope", emulator.captcha_answer]
    stored = tmp_path / "storage" / "downloads" / str(api.user["id"]) / "archive.rar"
    assert stored.read_bytes() == data
    assert not any("/api/" in c[1] for c in emulator.calls), "no account was involved"
    adapter = api.client.app.state.ctx.registry.get("rapidgator")
    assert adapter.free_sessions() == 0, "the single free slot was given back"


def test_widget_captcha_is_reported_and_completed_by_the_user_on_rapidgator(rapidgator, tmp_path):
    api, emulator = rapidgator
    data = payload(5000)
    emulator.files[RG_FILE] = ("archive.rar", data)
    emulator.captcha_mode = "widget"
    job_id = api.submit(rg_url())["created"][0]["id"]
    wait_captcha(api, job_id)
    challenge = api.get(f"/downloads/{job_id}/captcha").json()
    assert challenge == challenge | {"kind": "external", "has_image": False, "accepts_download_url": True}
    assert challenge["page_url"] == f"https://rapidgator.net/file/{RG_FILE}"
    # It cannot be answered inside FETCH, and arbitrary addresses are refused.
    assert api.post(f"/downloads/{job_id}/captcha", json={"answer": "x"}).status_code == 422
    for bad in ("https://example.com/download/x", rg_url(), "ftp://rapidgator.net/download/x"):
        assert api.post(f"/downloads/{job_id}/captcha", json={"download_url": bad}).status_code == 422
    assert emulator.captcha_posts == [] and emulator.free_transfers == 0
    # The user completed Rapidgator's steps in their browser and hands over the address.
    assert api.post(f"/downloads/{job_id}/captcha", json={"download_url": emulator.free_link(RG_FILE)}).status_code == 200
    assert api.wait_state(job_id, "completed", "failed")["state"] == "completed"
    assert (tmp_path / "storage" / "downloads" / str(api.user["id"]) / "archive.rar").read_bytes() == data


def test_captcha_left_unanswered_fails_without_retrying_around_it(rapidgator, monkeypatch):
    api, emulator = rapidgator
    monkeypatch.setattr(rapidgator_module, "CAPTCHA_TTL", 1.5)
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    job_id = api.submit(rg_url())["created"][0]["id"]
    wait_captcha(api, job_id)
    failed = api.wait_state(job_id, "failed")
    assert failed["error_code"] == "captcha_required" and "not completed in time" in failed["error_message"]
    assert emulator.captcha_posts == [] and emulator.free_transfers == 0
    assert api.get(f"/downloads/{job_id}/captcha").status_code == 404
    assert api.post(f"/downloads/{job_id}/captcha", json={"answer": "late"}).status_code == 404


def test_repeated_wrong_answers_fail_the_download(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    job_id = api.submit(rg_url())["created"][0]["id"]
    for attempt in range(1, rapidgator_module.CAPTCHA_ATTEMPTS + 1):
        wait_captcha(api, job_id)
        assert api.get(f"/downloads/{job_id}/captcha").json()["attempt"] == attempt
        api.post(f"/downloads/{job_id}/captcha", json={"answer": f"wrong-{attempt}"})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(emulator.captcha_posts) < attempt:
            time.sleep(0.05)
    failed = api.wait_state(job_id, "failed")
    assert failed["error_code"] == "captcha_required" and emulator.free_transfers == 0


def test_cancelling_a_waiting_free_download_frees_the_slot(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    job_id = api.submit(rg_url())["created"][0]["id"]
    wait_captcha(api, job_id)
    adapter = api.client.app.state.ctx.registry.get("rapidgator")
    assert adapter.free_sessions() == 1
    api.post(f"/downloads/{job_id}/cancel")
    assert adapter.free_sessions() == 0
    assert api.get(f"/downloads/{job_id}/captcha").status_code == 404


def test_only_one_free_download_runs_at_a_time(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("one.bin", payload(1000))
    emulator.files[RG_OTHER] = ("two.bin", payload(1000))
    first = api.submit(rg_url())["created"][0]["id"]
    wait_captcha(api, first)
    second = api.submit(rg_url(RG_OTHER))["created"][0]["id"]
    held = api.wait_state(second, "waiting")
    assert held["error_code"] == "wait_required" and "one free download at a time" in held["wait_reason"]
    starts = [c for c in emulator.calls if c[1] == "/download/AjaxStartTimer"]
    assert len(starts) == 1, "the second free download was not even started at Rapidgator"


# -- waiting periods and quotas stated by Rapidgator ----------------------------------


@pytest.mark.parametrize(
    "notice,state,code,fragment,minimum_wait",
    [
        ("Delay between downloads must be not less than 120 min. Don`t want to wait?", "waiting",
         "wait_required", "120 minutes", 119 * 60),
        ("You have reached your hourly downloads limit.", "waiting", "wait_required", "hourly", 59 * 60),
        ("You can`t download more than 1 file at a time in free mode.", "waiting", "wait_required",
         "one free download at a time", 60),
        ("You have reached your daily downloads limit. Upgrade!", "failed", "quota_exceeded", "daily", 0),
    ],
)
def test_free_mode_limits_are_reported_and_honoured(rapidgator, notice, state, code, fragment, minimum_wait):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    emulator.site_notice = notice
    job = api.wait_state(api.submit(rg_url())["created"][0]["id"], state)
    assert job["error_code"] == code
    assert fragment in (job["wait_reason"] or job["error_message"])
    if minimum_wait:
        from datetime import datetime

        until = datetime.fromisoformat(job["wait_until"].replace("Z", "+00:00"))
        assert (until - datetime.now(until.tzinfo)).total_seconds() >= minimum_wait
    assert not any(c[1].startswith("/download/") for c in emulator.calls), "the free download was not started"


def test_page_classification():
    adapter = RapidgatorProvider()
    codes = {}
    for label, status, text in [
        ("deleted", 200, "<h3>404 File not found</h3>"),
        ("gone", 404, ""),
        ("blocked", 200, "<title>Just a moment...</title>"),
        ("forbidden", 403, ""),
        ("down", 503, ""),
        ("maintenance", 200, "File is temporarily not available"),
        ("weird", 302, ""),
    ]:
        with pytest.raises(ProviderError) as caught:
            adapter.parse_file_page(status, text)
        codes[label] = caught.value.code
    assert codes == {
        "deleted": "not_found", "gone": "not_found", "blocked": "anonymous_unavailable",
        "forbidden": "anonymous_unavailable", "down": "provider_unavailable",
        "maintenance": "provider_unavailable", "weird": "unexpected_response",
    }
    page = adapter.parse_file_page(200, "<title>Download file a&amp;b.zip</title>File size: <strong>1.5 MB</strong>"
                                        "You can download files up to 2 GB in free mode")
    assert (page.name, page.size, page.premium_only, page.fid) == ("a&b.zip", int(1.5 * 1024**2), True, "")


def test_unrecognised_free_page_stops_instead_of_guessing(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    emulator._file_page = lambda file_id: emulator._html("<title>Download file archive.rar</title>")
    job = api.wait_state(api.submit(rg_url())["created"][0]["id"], "failed")
    assert job["error_code"] == "unexpected_response"


# -- account authentication -----------------------------------------------------------


def test_account_mode_downloads_through_the_api_and_anonymous_can_be_forced(rapidgator, tmp_path):
    api, emulator = rapidgator
    data = payload(50_000)
    emulator.files[RG_FILE] = ("archive.rar", data)
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "premium-pass"}})
    item = inspect(api, rg_url())
    assert item["auth_mode"] == "account" and item["supported"]
    assert api.wait_state(api.submit(rg_url())["created"][0]["id"], "completed", "failed")["state"] == "completed"
    assert not any(c[1].startswith(("/file/", "/download/")) for c in emulator.calls), "the free flow was not used"

    api.put("/providers/rapidgator/auth-mode", json={"mode": "anonymous"})
    emulator.calls.clear()
    assert inspect(api, rg_url())["auth_mode"] == "anonymous"
    assert not any("/api/" in c[1] for c in emulator.calls), "the stored account is not used in anonymous mode"


def test_invalid_credentials_are_reported_as_such(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "wrong"}})
    item = inspect(api, rg_url())
    assert item["supported"] is False and item["restriction"] == "auth_failed"
    report = api.post("/providers/rapidgator/test").json()
    assert report["ok"] is False
    auth = provider(api)["auth"]
    assert auth["account_valid"] is False and auth["account_message"]
    # Replacing the account resets the verdict until it is checked again.
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "premium-pass"}})
    assert provider(api)["auth"]["account_valid"] is None


def test_sessions_are_not_shared_between_different_passwords(rapidgator):
    api, emulator = rapidgator
    emulator.files[RG_FILE] = ("archive.rar", payload(1000))
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "premium-pass"}})
    assert inspect(api, rg_url())["supported"]
    api.put("/providers/rapidgator/credentials",
            json={"values": {"login": "premium@example.com", "password": "wrong"}})
    assert inspect(api, rg_url())["restriction"] == "auth_failed", "a cached session must not outlive its password"


# -- schema ---------------------------------------------------------------------------


def test_migration_upgrades_an_existing_database(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect as sa_inspect, text

    from fetch_app.runtime import resource_dir

    url = f"sqlite:///{tmp_path / 'old.db'}"
    cfg = Config(str(resource_dir() / "alembic.ini"))
    cfg.set_main_option("script_location", str(resource_dir() / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "0001")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO providers (id, enabled, health, health_message, updated_at) "
            "VALUES ('rapidgator', 1, 'unknown', '', '2026-01-01')"))
    run_migrations(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT auth_mode FROM providers")).scalar() == "auto"
    columns = {c["name"] for c in sa_inspect(engine).get_columns("provider_credentials")}
    assert {"valid", "validation_message", "validated_at"} <= columns
    engine.dispose()
