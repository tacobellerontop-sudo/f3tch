"""Rapidgator: anonymous free downloads, and accounts through the official API.

Why the first version needed an account
---------------------------------------
It only spoke Rapidgator's account API (https://rapidgator.net/article/api),
whose every call needs a session token from ``user/login``. Without stored
credentials it answered "CAPTCHA required" before making a single request, so
even a public link could not be inspected.

What this adapter does
----------------------
* Recognises Rapidgator file links.
* **Anonymous mode** follows the same free-download steps a visitor's browser
  performs on rapidgator.net: read the public file page (name, size, whether
  the file is Premium-only or gone), start the free-download timer, wait the
  full period Rapidgator announces, ask for the download step, and present the
  CAPTCHA Rapidgator demands **to the person who owns the download**. Their
  answer is submitted unchanged. One free download runs at a time.
* **Account mode** logs in with the stored account
  through Rapidgator's ``user/login`` API call (including a two-factor code
  when supplied) and requests a download address exactly as the account's
  plan permits.
* Reports waiting periods, hourly/daily limits, Premium-only files, missing
  files and rejected accounts as distinct, precise errors.

What it deliberately does not do
--------------------------------
* It never solves a CAPTCHA, sends one to a solving service, or skips it. A
  picture challenge is shown to the user; a widget bound to rapidgator.net
  cannot be shown inside FETCH at all and must be completed on Rapidgator's
  own page, after which the user may hand FETCH the address Rapidgator issued.
* It never shortens or skips a waiting period, and never retries around an
  hourly or daily limit, a one-download-at-a-time limit, or an IP restriction.
* It does not forge tokens or emulate Premium.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

from .base import (
    AuthMode,
    Capabilities,
    CaptchaChallenge,
    CredentialField,
    ErrorCode,
    HealthReport,
    LinkInfo,
    ProviderAdapter,
    ProviderContext,
    ProviderError,
    ResolvedDownload,
)

API = "https://rapidgator.net/api/v2"
SITE = "https://rapidgator.net"
_HOST = re.compile(r"^https?://(?:www\.)?(?:rapidgator\.(?:net|asia)|rg\.to)(?:/|$)", re.I)
_FILE = re.compile(
    r"^https?://(?:www\.)?(?:rapidgator\.(?:net|asia)|rg\.to)/file/([0-9a-f]{32}|\d+)(?:/([^/?#]+))?",
    re.I,
)
_FOLDER = re.compile(r"^https?://(?:www\.)?(?:rapidgator\.(?:net|asia)|rg\.to)/folder/", re.I)
_DOWNLOAD_HOSTS = re.compile(r"(?:^|\.)(?:rapidgator\.(?:net|asia)|rg\.to)$", re.I)
TOKEN_TTL = 20 * 60

#: How long a started free-download session stays usable after its timer ran out.
TIMER_GRACE = 10 * 60
#: How long the user has to complete a CAPTCHA before it is considered stale.
CAPTCHA_TTL = 5 * 60
#: A wrong answer gets a fresh challenge this many times before the job fails.
CAPTCHA_ATTEMPTS = 3
#: How often a job re-checks while another free download holds the single slot.
SLOT_RECHECK = 30
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 512 * 1024

PREMIUM_ACCOUNT_MESSAGE = (
    "Rapidgator only issues API download links to Premium accounts, and the account in "
    "use is not allowed to download this file. Switch Rapidgator to Anonymous mode "
    "under Providers to use the free download instead, or use a Premium account."
)

# -- the public file page ---------------------------------------------------------

_NOT_FOUND = re.compile(
    r"404 File not found|<title>\s*File not found|File not found\s*</h|"
    r"file (?:was|has been) (?:deleted|removed)|File is deleted|"
    r"This file (?:was|has been) blocked",
    re.I,
)
_TEMP_UNAVAILABLE = re.compile(r"temporar(?:il)?y (?:not available|unavailable)|server maintenance", re.I)
_BLOCKED = re.compile(
    r"Just a moment\.\.\.|cf-browser-verification|challenge-platform|"
    r"Your IP(?: address)? (?:is|has been) (?:blocked|banned)|Access from your (?:country|IP)",
    re.I,
)
_PREMIUM_ONLY = re.compile(
    # Only Rapidgator's exact refusals: its pages also advertise Premium, and an
    # advertisement must never be mistaken for a restriction on this file.
    r"can be downloaded by premium only|"
    r"You can download files up to [^<]{1,24} in free mode",
    re.I,
)
_DELAY = re.compile(r"Delay between downloads must be not less than\s*(\d+)\s*min", re.I)
_DAILY = re.compile(r"reached your daily downloads? limit", re.I)
_HOURLY = re.compile(r"reached your hourly downloads? limit", re.I)
_PARALLEL = re.compile(r"download (?:not )?more than (?:\d+|one) files? at a time", re.I)
_TITLE = re.compile(r"<title>\s*Download file\s+([^<]+?)\s*</title>", re.I)
_NAME = re.compile(r"Downloading:\s*</p>\s*<a[^>]*>\s*([^<]+?)\s*</a>", re.I | re.S)
_SIZE = re.compile(r"File size:\s*<strong>\s*([\d.,]+)\s*([KMGT]?B)\s*</strong>", re.I)
_VAR = r"var\s+{0}\s*=\s*['\"]?([^'\";\s]+)"
_FID = re.compile(_VAR.format("fid"))
_SECS = re.compile(_VAR.format("secs"))

# -- the CAPTCHA step -------------------------------------------------------------

_DOWNLOAD_LINK = re.compile(
    r"""(?:return|location\.href\s*=|href=)\s*['"](https?://[^'"\s]+/download/[^'"\s]*)['"]""", re.I
)
#: Pages of the free-download steps themselves, which are not the download address.
_STEP = re.compile(r"/download/(?:captcha|ajax)", re.I)
_WIDGET = re.compile(
    r"g-recaptcha|recaptcha/api|h-captcha|hcaptcha\.com|cf-turnstile|solvemedia|"
    r"challenges\.cloudflare\.com",
    re.I,
)
_FORM = re.compile(r"<form([^>]*)>(.*?)</form>", re.I | re.S)
_ATTR = r"""{0}\s*=\s*["']([^"']*)["']"""
_INPUT = re.compile(r"<input([^>]*)>", re.I)
_IMG = re.compile(r"""<img[^>]+src\s*=\s*["']([^"']*captcha[^"']*)["']""", re.I)
_WRONG = re.compile(r"verification code is incorrect|wrong captcha|captcha (?:is )?(?:wrong|incorrect)", re.I)
_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def _attr(tag: str, name: str) -> str:
    match = re.search(_ATTR.format(name), tag, re.I)
    return html_lib.unescape(match.group(1)) if match else ""


@dataclass
class FilePage:
    """What Rapidgator's public file page says about one file."""

    name: str = ""
    size: int | None = None
    premium_only: bool = False
    fid: str = ""
    seconds: int = 0


@dataclass
class _FreeSession:
    """Rapidgator's own free-download session for one job, kept across waits."""

    key: str
    file_id: str
    cookies: dict[str, str] = field(default_factory=dict)
    name: str = ""
    size: int | None = None
    sid: str = ""
    stage: str = "new"  # new -> timer -> captcha -> transfer
    ready_at: float = 0.0
    expires_at: float = 0.0
    challenge: CaptchaChallenge | None = None
    form_action: str = ""
    form_fields: dict[str, str] = field(default_factory=dict)
    answer_field: str = ""
    attempts: int = 0


class RapidgatorProvider(ProviderAdapter):
    id = "rapidgator"
    name = "Rapidgator"
    description = (
        "Rapidgator file links: free downloads without an account, or your own "
        "account through the official API."
    )
    homepage = "https://rapidgator.net"
    priority = 10
    capabilities = Capabilities(
        anonymous=True, credentials=True, requires_credentials=False, checksum=True,
        waits=True, captcha=True,
    )
    auth_modes = (AuthMode.ANONYMOUS, AuthMode.ACCOUNT)
    #: Rapidgator serves one free download at a time per address.
    anonymous_max_concurrent = 1
    credential_fields = (
        CredentialField("login", "Account e-mail", kind="text", required=True),
        CredentialField("password", "Password", required=True),
        CredentialField(
            "code",
            "Two-factor code",
            kind="text",
            help="Only if the account uses two-factor authentication. Codes expire, so "
            "re-enter a fresh one when Rapidgator asks again.",
        ),
    )
    notes = (
        "Without an account, FETCH uses Rapidgator's free download: it waits the full "
        "period Rapidgator announces, then asks you to complete Rapidgator's CAPTCHA. "
        "FETCH never solves or skips the CAPTCHA and never shortens a wait.",
        "A picture CAPTCHA can be answered inside FETCH. A CAPTCHA widget that only works "
        "on rapidgator.net must be completed there in your own browser; you can then "
        "paste the download address Rapidgator gives you. That address is tied to the "
        "network address that requested it, so this works when your browser and FETCH "
        "share one.",
        "Free downloads run one at a time, cannot be resumed, and are subject to "
        "Rapidgator's size, hourly and daily limits. Premium-only files need a Premium "
        "account.",
        "With an account, downloads use Rapidgator's official API. Rapidgator only issues "
        "API download links to accounts whose plan allows it (in practice: Premium).",
        "Folder links are not supported.",
    )
    verification = (
        "The account API is implemented against Rapidgator's published API v2; the "
        "anonymous flow follows the public free-download pages as documented by "
        "open-source download managers. Both are covered by automated tests against a "
        "local emulation. NOT verified against the live service: Rapidgator's pages "
        "change, and anything FETCH does not recognise is reported as an unexpected "
        "response rather than guessed at."
    )

    def __init__(self, api_url: str = API, site_url: str = SITE) -> None:
        self.api_url = api_url
        self.site_url = site_url.rstrip("/")
        self._tokens: dict[str, tuple[str, float]] = {}  # credential fingerprint -> token, obtained-at
        self._free: dict[str, _FreeSession] = {}
        self._lapsed: set[str] = set()
        self._now = time.monotonic

    def can_handle(self, url: str) -> bool:
        return bool(_HOST.match(url))

    def _parse(self, url: str) -> tuple[str, str]:
        if _FOLDER.match(url):
            raise ProviderError(
                ErrorCode.UNSUPPORTED,
                "Rapidgator folder links are not supported. Add links to the individual files.",
            )
        match = _FILE.match(url)
        if not match:
            raise ProviderError(ErrorCode.UNSUPPORTED, "This is not a Rapidgator file link.")
        name = unquote(match.group(2) or "")
        if name.lower().endswith(".html"):
            name = name[:-5]
        return match.group(1), name

    # -- API plumbing ----------------------------------------------------------

    @staticmethod
    def _error(status: int, details: str) -> ProviderError:
        text = (details or "").strip()
        lower = text.lower()
        if "captcha" in lower:
            return ProviderError(
                ErrorCode.CAPTCHA_REQUIRED,
                "Rapidgator's API demands a CAPTCHA for this account, which can only be "
                "completed by signing in on rapidgator.net. FETCH does not solve it.",
            )
        if status == 404 or "not found" in lower:
            return ProviderError(
                ErrorCode.NOT_FOUND,
                "Rapidgator reports that this file does not exist or was deleted.",
            )
        if any(w in lower for w in ("traffic", "limit", "quota", "exceeded", "bandwidth")):
            return ProviderError(
                ErrorCode.QUOTA_EXCEEDED,
                f"Rapidgator refused the download because of an account limit: {text[:200]}",
            )
        if any(w in lower for w in ("premium", "subscription", "upgrade")):
            return ProviderError(ErrorCode.PREMIUM_ONLY, PREMIUM_ACCOUNT_MESSAGE)
        if status in (401, 403, 406, 423) or "denied" in lower or "password" in lower:
            return ProviderError(
                ErrorCode.AUTH_FAILED,
                "Rapidgator rejected the account"
                + (f": {text[:200]}" if text else "."),
            )
        if status in (429, 500, 502, 503, 504):
            return ProviderError(ErrorCode.UNAVAILABLE, "Rapidgator's API is temporarily unavailable.", retryable=True)
        return ProviderError(
            ErrorCode.PROTOCOL,
            f"Rapidgator returned an unexpected response ({status})"
            + (f": {text[:200]}" if text else "."),
        )

    async def _call(self, ctx: ProviderContext, path: str, params: dict[str, str]) -> dict:
        http_status, payload = await ctx.http.get_json("GET", f"{self.api_url}/{path}", params=params)
        if not isinstance(payload, dict):
            raise self._error(http_status if http_status != 200 else 502, "")
        status = payload.get("status", http_status)
        status = int(status) if str(status).isdigit() else http_status
        response = payload.get("response")
        if status != 200 or not isinstance(response, dict):
            raise self._error(status, str(payload.get("details") or ""))
        return response

    @staticmethod
    def _has_account(ctx: ProviderContext) -> bool:
        creds = ctx.credentials or {}
        return bool(creds.get("login") and creds.get("password"))

    def _credentials(self, ctx: ProviderContext) -> dict[str, str]:
        if not self._has_account(ctx):
            raise ProviderError(ErrorCode.AUTH_REQUIRED, "No Rapidgator account is configured.")
        return ctx.credentials or {}

    @staticmethod
    def _fingerprint(creds: dict[str, str]) -> str:
        # Sessions are cached per exact credentials, never per login name alone:
        # two FETCH users must not share a session by naming the same account.
        raw = "\0".join((creds["login"], creds["password"], creds.get("code", "")))
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _login(self, ctx: ProviderContext, *, force: bool = False) -> tuple[str, dict]:
        creds = self._credentials(ctx)
        fingerprint = self._fingerprint(creds)
        cached = self._tokens.get(fingerprint)
        if not force and cached and time.monotonic() - cached[1] < TOKEN_TTL:
            return cached[0], {}
        params = {"login": creds["login"], "password": creds["password"]}
        if creds.get("code"):
            params["code"] = creds["code"]
        response = await self._call(ctx, "user/login", params)
        token = response.get("token")
        if not isinstance(token, str) or not token:
            raise ProviderError(ErrorCode.AUTH_FAILED, "Rapidgator did not return a session for this account.")
        self._tokens[fingerprint] = (token, time.monotonic())
        user = response.get("user")
        return token, user if isinstance(user, dict) else {}

    async def _authed(self, ctx: ProviderContext, path: str, params: dict[str, str]) -> dict:
        token, _ = await self._login(ctx)
        try:
            return await self._call(ctx, path, {**params, "token": token})
        except ProviderError as exc:
            if exc.code != ErrorCode.AUTH_FAILED:
                raise
        # The cached session may simply have expired: log in once more.
        token, _ = await self._login(ctx, force=True)
        return await self._call(ctx, path, {**params, "token": token})

    # -- anonymous free download: page plumbing ---------------------------------

    def _site_host(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        own = (urlsplit(self.site_url).hostname or "").lower()
        return host == own or bool(_DOWNLOAD_HOSTS.search(host))

    async def _site(
        self,
        ctx: ProviderContext,
        cookies: dict[str, str],
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        ajax: bool = False,
        max_bytes: int = MAX_PAGE_BYTES,
    ) -> tuple[int, str, bytes]:
        """One request to rapidgator.net as an ordinary visitor.

        Cookies Rapidgator sets are kept in ``cookies`` (its session), and
        redirects are followed only within Rapidgator's own hosts.
        Returns ``(status, content-type, body)``.
        """
        url = urljoin(self.site_url + "/", path)
        for _hop in range(6):
            if not self._site_host(url):
                raise ProviderError(
                    ErrorCode.PROTOCOL, "Rapidgator redirected to an unexpected address."
                )
            headers = {"Accept-Encoding": "identity"}
            if cookies:
                headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            if ajax:
                headers["X-Requested-With"] = "XMLHttpRequest"
                headers["Accept"] = "application/json, text/javascript, */*"
            async with ctx.http.request(
                method, url, headers=headers, follow_redirects=False, params=params, data=data
            ) as response:
                for name, morsel in response.cookies.items():
                    cookies[name] = morsel.value
                if response.status in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                    url = urljoin(url, response.headers["Location"])
                    if response.status == 303 or (response.status in (301, 302) and method == "POST"):
                        method, data = "GET", None
                    params = None
                    continue
                chunks: list[bytes] = []
                received = 0
                try:
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        chunks.append(chunk)
                        received += len(chunk)
                        if received >= max_bytes:
                            break
                except Exception as exc:  # aiohttp/timeout errors while reading
                    raise ProviderError(
                        ErrorCode.NETWORK, "Rapidgator did not answer in time.", retryable=True
                    ) from exc
                return response.status, response.headers.get("Content-Type", ""), b"".join(chunks)
        raise ProviderError(ErrorCode.PROTOCOL, "Rapidgator redirected too many times.")

    async def _ajax(
        self, ctx: ProviderContext, session: _FreeSession, path: str, params: dict[str, str]
    ) -> dict:
        status, _, body = await self._site(ctx, session.cookies, "GET", path, params=params, ajax=True)
        text = body.decode("utf-8", "replace")
        self._raise_for_page(status, text)
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise ProviderError(
                ErrorCode.PROTOCOL, "Rapidgator's free download answered in an unexpected format."
            )
        return payload

    # -- anonymous free download: reading Rapidgator's answers --------------------

    @staticmethod
    def _limit_error(text: str) -> ProviderError | None:
        """Free-mode restrictions Rapidgator states in a page or message."""
        delay = _DELAY.search(text)
        if delay:
            minutes = max(1, min(int(delay.group(1)), 24 * 60))
            return ProviderError(
                ErrorCode.WAIT_REQUIRED,
                f"Rapidgator requires {minutes} minutes between free downloads. "
                "FETCH is waiting that period out.",
                wait_seconds=minutes * 60,
            )
        if _DAILY.search(text):
            return ProviderError(
                ErrorCode.QUOTA_EXCEEDED,
                "Rapidgator's daily limit for free downloads from this network address has "
                "been reached. Try again tomorrow, or use a Rapidgator account.",
            )
        if _HOURLY.search(text):
            return ProviderError(
                ErrorCode.WAIT_REQUIRED,
                "Rapidgator's hourly limit for free downloads has been reached. FETCH will "
                "try again in an hour.",
                wait_seconds=3600,
            )
        if _PARALLEL.search(text):
            return ProviderError(
                ErrorCode.WAIT_REQUIRED,
                "Rapidgator allows one free download at a time from this network address, "
                "and another one is running. FETCH will try again shortly.",
                wait_seconds=120,
            )
        return None

    @staticmethod
    def _raise_for_page(status: int, text: str) -> None:
        """Conditions that apply to any page of the free flow."""
        if _BLOCKED.search(text) or status in (401, 403, 451):
            raise ProviderError(
                ErrorCode.ANON_UNAVAILABLE,
                "Rapidgator refused anonymous access from this server's network address "
                "(an access check or address restriction). FETCH does not work around it. "
                "Use a Rapidgator account, or download from a browser.",
            )
        if status == 429 or status >= 500 or _TEMP_UNAVAILABLE.search(text):
            raise ProviderError(
                ErrorCode.UNAVAILABLE, "Rapidgator is temporarily unavailable.", retryable=True
            )

    def parse_file_page(self, status: int, text: str, url_name: str = "") -> FilePage:
        """Read the public file page. Raises for files that are gone or unreachable."""
        if status == 404 or status == 410 or _NOT_FOUND.search(text):
            raise ProviderError(
                ErrorCode.NOT_FOUND,
                "Rapidgator reports that this file does not exist or was deleted.",
            )
        self._raise_for_page(status, text)
        if status != 200:
            raise ProviderError(
                ErrorCode.PROTOCOL, f"Rapidgator returned an unexpected response ({status})."
            )
        page = FilePage(name=url_name)
        name = _NAME.search(text) or _TITLE.search(text)
        if name:
            page.name = html_lib.unescape(name.group(1)).strip()
        size = _SIZE.search(text)
        if size:
            try:
                number = float(size.group(1).replace(",", "."))
                page.size = int(number * _UNITS[size.group(2).upper()])
            except ValueError:
                page.size = None
        page.premium_only = bool(_PREMIUM_ONLY.search(text))
        fid = _FID.search(text)
        page.fid = fid.group(1) if fid else ""
        secs = _SECS.search(text)
        page.seconds = int(secs.group(1)) if secs and secs.group(1).isdigit() else 0
        return page

    async def _file_page(self, ctx: ProviderContext, cookies: dict[str, str], file_id: str, url_name: str) -> tuple[FilePage, str]:
        status, _, body = await self._site(ctx, cookies, "GET", f"/file/{file_id}")
        text = body.decode("utf-8", "replace")
        return self.parse_file_page(status, text, url_name), text

    @staticmethod
    def _premium_only_error() -> ProviderError:
        return ProviderError(
            ErrorCode.PREMIUM_ONLY,
            "Rapidgator only offers this file to Premium accounts (it is Premium-only or "
            "larger than the free download limit). It cannot be downloaded anonymously.",
        )

    # -- anonymous free download: the session -------------------------------------

    def _expire(self, now: float) -> None:
        for key, session in list(self._free.items()):
            if session.expires_at and now >= session.expires_at:
                del self._free[key]
                if session.stage == "captcha":
                    self._lapsed.add(key)

    def check_download_address(self, url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not self._site_host(url):
            return (
                "That is not a Rapidgator download address. Paste the address Rapidgator "
                "shows after its CAPTCHA."
            )
        if _FILE.match(url) or "/download/" not in parts.path or _STEP.search(parts.path):
            return (
                "That is the link to the file's page, not the download address. Paste the "
                "address Rapidgator shows after its CAPTCHA."
            )
        return None

    def find_download_address(self, page: str) -> str | None:
        """The download address on a Rapidgator page the user reached after its CAPTCHA."""
        if _WRONG.search(page):
            return None
        for match in _DOWNLOAD_LINK.finditer(page):
            address = html_lib.unescape(match.group(1))
            if self.check_download_address(address) is None:
                return address
        return None

    def release_job(self, job_id: int) -> None:
        self._free.pop(f"job:{job_id}", None)
        self._lapsed.discard(f"job:{job_id}")

    def free_sessions(self) -> int:
        """Number of free-download sessions currently holding the single slot."""
        self._expire(self._now())
        return len(self._free)

    def _resolved_free(self, session: _FreeSession, download_url: str) -> ResolvedDownload:
        session.stage, session.challenge = "transfer", None
        # Held until the transfer ends (see ``download``); the short expiry only
        # covers a job that is rejected before its transfer starts.
        session.expires_at = self._now() + 120

        def map_error(status: int, _headers: dict, body: str) -> ProviderError | None:
            limit = self._limit_error(body)
            if limit is not None:
                return limit
            if status in (404, 410):
                return ProviderError(
                    ErrorCode.UNAVAILABLE,
                    "Rapidgator's free download address expired before the transfer "
                    "started. The free download has to be requested again.",
                    retryable=True,
                )
            if status == 403:
                return ProviderError(
                    ErrorCode.ANON_UNAVAILABLE,
                    "Rapidgator refused the free download address. It is tied to the "
                    "network address that requested it.",
                )
            if status == 429 or status >= 500:
                return ProviderError(
                    ErrorCode.UNAVAILABLE, "Rapidgator's file server is temporarily unavailable.",
                    retryable=True,
                )
            return None

        headers = {}
        if session.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in session.cookies.items())
        return ResolvedDownload(
            url=download_url,
            filename=session.name,
            # The page only states a rounded size ("1.5 GB"); the exact one comes
            # from the file server, where FETCH's size limits are enforced too.
            size=None,
            headers=headers,
            # Free downloads are single-connection and cannot be resumed.
            resumable=False,
            allow_aria2=False,
            identity=f"rapidgator-free:{session.file_id}",
            error_mapper=map_error,
        )

    async def _challenge(self, ctx: ProviderContext, session: _FreeSession, text: str, page_url: str) -> CaptchaChallenge:
        """Describe the CAPTCHA on Rapidgator's page so the user can complete it."""
        if _WIDGET.search(text):
            return CaptchaChallenge(
                kind="external",
                prompt=(
                    "Rapidgator protects this free download with a CAPTCHA that only works "
                    "on rapidgator.net, so FETCH cannot show it here and will not solve it. "
                    "Open the file on Rapidgator, complete the free download steps there, "
                    "and paste the download address Rapidgator gives you."
                ),
                expires_in=CAPTCHA_TTL,
                page_url=page_url,
                accepts_download_url=True,
            )
        image = _IMG.search(text)
        form = next(
            (m for m in _FORM.finditer(text) if "captcha" in m.group(0).lower()), None
        )
        if not image or not form:
            raise ProviderError(
                ErrorCode.PROTOCOL,
                "Rapidgator's CAPTCHA step did not look as expected, so FETCH stopped "
                "rather than guess. Download this file from a browser instead.",
            )
        fields: dict[str, str] = {}
        answer_field = ""
        for tag in _INPUT.findall(form.group(2)):
            name, kind = _attr(tag, "name"), _attr(tag, "type").lower()
            if not name:
                continue
            if kind == "hidden":
                fields[name] = _attr(tag, "value")
            elif kind in ("text", "") and not answer_field:
                answer_field = name
        if not answer_field:
            raise ProviderError(
                ErrorCode.PROTOCOL, "Rapidgator's CAPTCHA form has no answer field FETCH recognises."
            )
        status, content_type, body = await self._site(
            ctx, session.cookies, "GET", html_lib.unescape(image.group(1)), max_bytes=MAX_IMAGE_BYTES
        )
        if status != 200 or not body or not content_type.lower().startswith("image/"):
            raise ProviderError(ErrorCode.PROTOCOL, "Rapidgator did not serve its CAPTCHA picture.")
        session.form_action = _attr(form.group(1), "action") or "/download/captcha"
        session.form_fields, session.answer_field = fields, answer_field
        return CaptchaChallenge(
            kind="image",
            prompt="Type the characters from Rapidgator's picture to start the free download.",
            expires_in=CAPTCHA_TTL,
            image=body,
            image_type=content_type.split(";")[0].strip().lower(),
            page_url=page_url,
            accepts_download_url=True,
            attempt=session.attempts + 1,
        )

    async def _after_captcha_page(
        self, ctx: ProviderContext, session: _FreeSession, status: int, text: str, page_url: str
    ) -> ResolvedDownload:
        """Rapidgator's answer to the CAPTCHA step: a download address or a challenge."""
        self._raise_for_page(status, text)
        link = _DOWNLOAD_LINK.search(text)
        if link and self._site_host(link.group(1)) and not _WRONG.search(text):
            return self._resolved_free(session, html_lib.unescape(link.group(1)))
        limit = self._limit_error(text)
        if limit is not None:
            raise limit
        if session.attempts >= CAPTCHA_ATTEMPTS:
            raise ProviderError(
                ErrorCode.CAPTCHA_REQUIRED,
                f"Rapidgator rejected the CAPTCHA answer {CAPTCHA_ATTEMPTS} times. Retry the "
                "download to start again.",
            )
        challenge = await self._challenge(ctx, session, text, page_url)
        session.stage, session.challenge = "captcha", challenge
        session.expires_at = self._now() + challenge.expires_in
        wrong = session.attempts > 0
        raise ProviderError(
            ErrorCode.CAPTCHA_REQUIRED,
            ("Rapidgator did not accept that answer. " if wrong else "")
            + "Rapidgator requires a CAPTCHA before this free download. Complete it to continue.",
            challenge=challenge,
        )

    async def _start_free(self, ctx: ProviderContext, key: str, file_id: str, url_name: str) -> _FreeSession:
        session = _FreeSession(key=key, file_id=file_id)
        page, text = await self._file_page(ctx, session.cookies, file_id, url_name)
        session.name, session.size = page.name, page.size
        if page.premium_only:
            raise self._premium_only_error()
        limit = self._limit_error(text)
        if limit is not None:
            raise limit
        if not page.fid:
            raise ProviderError(
                ErrorCode.PROTOCOL,
                "Rapidgator's page for this file did not offer a free download in a form "
                "FETCH recognises, so it stopped rather than guess.",
            )
        started = await self._ajax(ctx, session, "/download/AjaxStartTimer", {"fid": page.fid})
        sid = started.get("sid")
        if started.get("state") != "started" or not isinstance(sid, str) or not sid:
            raise self._ajax_error(started)
        session.sid, session.stage = sid, "timer"
        session.ready_at = self._now() + max(0, page.seconds)
        session.expires_at = session.ready_at + TIMER_GRACE
        return session

    def _ajax_error(self, payload: dict) -> ProviderError:
        text = " ".join(str(payload.get(k) or "") for k in ("message", "error", "code", "state"))
        return self._limit_error(text) or ProviderError(
            ErrorCode.PROTOCOL,
            "Rapidgator did not start the free download"
            + (f": {text.strip()[:200]}" if text.strip() else "."),
        )

    async def _resolve_free(self, url: str, file_id: str, url_name: str, ctx: ProviderContext) -> ResolvedDownload:
        key = f"job:{ctx.job_id}" if ctx.job_id is not None else f"file:{file_id}"
        page_url = f"https://rapidgator.net/file/{file_id}"
        supplied = ctx.user_input or {}
        now = self._now()
        self._expire(now)
        session = self._free.get(key)
        try:
            if key in self._lapsed:
                self._lapsed.discard(key)
                if not supplied:
                    raise ProviderError(
                        ErrorCode.CAPTCHA_REQUIRED,
                        "Rapidgator's CAPTCHA was not completed in time. Retry the download "
                        "to get a new one.",
                    )
            if session is None and any(k != key for k in self._free):
                raise ProviderError(
                    ErrorCode.WAIT_REQUIRED,
                    "Rapidgator allows one free download at a time. This one starts when "
                    "the free download before it has finished.",
                    wait_seconds=SLOT_RECHECK,
                )

            handed_over = (supplied.get("download_url") or "").strip()
            if handed_over:
                # The user completed Rapidgator's free steps in their own browser.
                problem = self.check_download_address(handed_over)
                if problem:
                    raise ProviderError(ErrorCode.UNSUPPORTED, problem)
                if session is None:
                    session = _FreeSession(key=key, file_id=file_id, name=url_name)
                    self._free[key] = session
                session.cookies = {}
                return self._resolved_free(session, handed_over)

            if session is None:
                session = await self._start_free(ctx, key, file_id, url_name)
                self._free[key] = session
                seconds = max(1, int(session.ready_at - self._now()) + 1)
                raise ProviderError(
                    ErrorCode.WAIT_REQUIRED,
                    f"Rapidgator's free download starts after a {seconds}-second waiting period.",
                    wait_seconds=seconds,
                )

            if session.stage == "timer":
                remaining = session.ready_at - now
                if remaining > 0:
                    raise ProviderError(
                        ErrorCode.WAIT_REQUIRED,
                        "Rapidgator's waiting period for this free download is still running.",
                        wait_seconds=remaining + 0.5,
                    )
                answer = await self._ajax(ctx, session, "/download/AjaxGetDownloadLink", {"sid": session.sid})
                if answer.get("state") != "done":
                    raise self._ajax_error(answer)
                status, _, body = await self._site(ctx, session.cookies, "GET", "/download/captcha")
                return await self._after_captcha_page(
                    ctx, session, status, body.decode("utf-8", "replace"), page_url
                )

            if session.stage == "captcha" and session.challenge is not None:
                typed = (supplied.get("answer") or "").strip()
                if typed and session.challenge.kind == "image":
                    session.attempts += 1
                    status, _, body = await self._site(
                        ctx, session.cookies, "POST", session.form_action,
                        data={**session.form_fields, session.answer_field: typed},
                    )
                    return await self._after_captcha_page(
                        ctx, session, status, body.decode("utf-8", "replace"), page_url
                    )
                # Still waiting for the user (for example after a pause).
                challenge = session.challenge
                challenge.expires_in = max(1.0, session.expires_at - now)
                raise ProviderError(
                    ErrorCode.CAPTCHA_REQUIRED,
                    "Rapidgator requires a CAPTCHA before this free download. Complete it to continue.",
                    challenge=challenge,
                )

            # A session left in the transfer stage (the transfer was interrupted):
            # Rapidgator's address is single-use, so the free download starts over.
            self._free.pop(key, None)
            raise ProviderError(
                ErrorCode.UNAVAILABLE,
                "The free download was interrupted and has to be requested from Rapidgator again.",
                retryable=True,
            )
        except ProviderError as exc:
            if exc.wait_seconds is None and exc.challenge is None:
                self._free.pop(key, None)  # terminal: give the slot back
            raise

    # -- adapter interface -----------------------------------------------------

    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        file_id, url_name = self._parse(url)
        if not self._has_account(ctx):
            # Public metadata needs no account: read Rapidgator's file page.
            page, _ = await self._file_page(ctx, {}, file_id, url_name)
            info = LinkInfo(
                url=url, provider=self.id, filename=page.name or url_name, size=page.size,
                resumable=False, premium_only=page.premium_only,
            )
            if page.premium_only:
                error = self._premium_only_error()
                info.supported, info.restriction, info.message = False, error.code, error.message
            else:
                info.message = (
                    "Free download: Rapidgator's waiting period applies and you will be "
                    "asked to complete its CAPTCHA."
                )
            return info
        response = await self._authed(ctx, "file/info", {"file_id": file_id})
        file = response.get("file") if isinstance(response.get("file"), dict) else response
        size = str(file.get("size", ""))
        return LinkInfo(
            url=url,
            provider=self.id,
            filename=str(file.get("name") or url_name),
            size=int(size) if size.isdigit() else None,
            resumable=True,
        )

    async def resolve(self, url: str, ctx: ProviderContext) -> ResolvedDownload:
        file_id, url_name = self._parse(url)
        if not self._has_account(ctx):
            return await self._resolve_free(url, file_id, url_name, ctx)
        info = await self._authed(ctx, "file/info", {"file_id": file_id})
        file = info.get("file") if isinstance(info.get("file"), dict) else info
        response = await self._authed(ctx, "file/download", {"file_id": file_id})

        delay = str(response.get("delay") or "0")
        if delay.isdigit() and int(delay) > 0:
            raise ProviderError(
                ErrorCode.WAIT_REQUIRED,
                "Rapidgator requires a waiting period before this download can start.",
                wait_seconds=min(int(delay), 24 * 3600),
            )
        download_url = response.get("download_url") or response.get("url")
        if not isinstance(download_url, str) or not download_url.startswith("http"):
            raise ProviderError(ErrorCode.PREMIUM_ONLY, PREMIUM_ACCOUNT_MESSAGE)
        size = str(file.get("size", ""))
        md5 = str(file.get("hash") or "").lower()
        return ResolvedDownload(
            url=download_url,
            filename=str(file.get("name") or url_name),
            size=int(size) if size.isdigit() else None,
            checksum=("md5", md5) if re.fullmatch(r"[0-9a-f]{32}", md5) else None,
            identity=f"rapidgator:{file_id}:{md5 or size}",
        )

    async def download(self, transfer, resolved: ResolvedDownload) -> None:
        key = f"job:{transfer.job_id}"
        session = self._free.get(key)
        if session is None or session.stage != "transfer":
            await transfer.fetch(resolved)
            return
        session.expires_at = 0.0  # hold the single free slot for the whole transfer
        try:
            await transfer.fetch(resolved)
        finally:
            self._free.pop(key, None)

    async def authenticate(self, ctx: ProviderContext) -> HealthReport:
        if not self._has_account(ctx):
            return HealthReport(ok=False, message="No Rapidgator account is configured.")
        try:
            _, user = await self._login(ctx, force=True)
        except ProviderError as exc:
            return HealthReport(ok=False, message=exc.message)
        premium = bool(user.get("is_premium"))
        traffic = user.get("traffic") if isinstance(user.get("traffic"), dict) else {}
        account = {"premium": premium, "traffic_left": traffic.get("left")}
        if premium:
            return HealthReport(ok=True, message="Signed in. Premium account.", account=account)
        return HealthReport(
            ok=True,
            message="Signed in, but this is not a Premium account. Rapidgator normally "
            "refuses API downloads for free accounts; use Anonymous mode for free downloads.",
            account=account,
        )

    async def test(self, ctx: ProviderContext) -> HealthReport:
        if self._has_account(ctx):
            return await self.authenticate(ctx)
        try:
            status, _, body = await self._site(ctx, {}, "GET", "/")
            self._raise_for_page(status, body.decode("utf-8", "replace"))
        except ProviderError as exc:
            return HealthReport(ok=False, message=exc.message)
        if status != 200:
            return HealthReport(ok=False, message=f"Rapidgator answered unexpectedly ({status}).")
        return HealthReport(
            ok=True,
            message="Rapidgator is reachable. No account is configured: anonymous free "
            "downloads are available, with Rapidgator's waiting period and CAPTCHA.",
        )
