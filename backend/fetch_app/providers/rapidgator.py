"""Rapidgator, through its official account API (https://rapidgator.net/article/api).

What this adapter does
----------------------
* Recognises Rapidgator file links.
* Logs in with the account the administrator configured, using Rapidgator's
  own ``user/login`` API call (including a two-factor code when supplied).
* Reads file metadata and requests a download address through the API, exactly
  as the account's plan permits.
* Honours any delay the API announces, and reports quota, traffic and account
  restrictions as they are.

What it deliberately does not do
--------------------------------
* It does not automate Rapidgator's free web download page. That flow ends in
  an interactive CAPTCHA bound to rapidgator.net, which FETCH will not solve or
  bypass; such links are reported with ``captcha_required``.
* It does not forge tokens, emulate Premium, or evade IP/account limits.
"""

from __future__ import annotations

import re
import time
from urllib.parse import unquote

from .base import (
    Capabilities,
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
_HOST = re.compile(r"^https?://(?:www\.)?(?:rapidgator\.(?:net|asia)|rg\.to)(?:/|$)", re.I)
_FILE = re.compile(
    r"^https?://(?:www\.)?(?:rapidgator\.(?:net|asia)|rg\.to)/file/([0-9a-f]{32}|\d+)(?:/([^/?#]+))?",
    re.I,
)
_FOLDER = re.compile(r"^https?://(?:www\.)?(?:rapidgator\.(?:net|asia)|rg\.to)/folder/", re.I)
TOKEN_TTL = 20 * 60

FREE_WORKFLOW_MESSAGE = (
    "Rapidgator's free download requires waiting on rapidgator.net and solving an "
    "interactive CAPTCHA there. FETCH does not solve or bypass CAPTCHAs, so this link "
    "cannot be fetched automatically. An administrator can configure an authorized "
    "Rapidgator account under Providers."
)


class RapidgatorProvider(ProviderAdapter):
    id = "rapidgator"
    name = "Rapidgator"
    description = "Rapidgator file links, through the official API with your own account."
    homepage = "https://rapidgator.net"
    priority = 10
    capabilities = Capabilities(
        anonymous=False, credentials=True, requires_credentials=True, checksum=True,
        waits=True, captcha=True,
    )
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
        "Downloads use Rapidgator's official API with the account you configure. "
        "Rapidgator only issues API download links to accounts whose plan allows it "
        "(in practice: Premium).",
        "Without an account, or when the account's plan does not allow API downloads, "
        "Rapidgator's free flow requires an interactive CAPTCHA on rapidgator.net. FETCH "
        "reports this as 'CAPTCHA required' and does not attempt to get around it.",
        "Waiting periods announced by Rapidgator are waited out; traffic and daily "
        "quotas are reported, never evaded.",
        "Folder links are not supported.",
    )
    verification = (
        "Implemented against Rapidgator's published API v2. Login, token reuse, metadata, "
        "delay handling and error mapping are covered by automated tests against a local "
        "emulation of that API. NOT verified against the live service or a real Premium "
        "account: confirm with 'Test connection' after entering your credentials."
    )

    def __init__(self, api_url: str = API) -> None:
        self.api_url = api_url
        self._token: tuple[str, float, str] | None = None  # token, obtained-at, login

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
            return ProviderError(ErrorCode.CAPTCHA_REQUIRED, FREE_WORKFLOW_MESSAGE)
        if status == 404 or "not found" in lower:
            return ProviderError(ErrorCode.NOT_FOUND, "Rapidgator reports that this file does not exist.")
        if any(w in lower for w in ("traffic", "limit", "quota", "exceeded", "bandwidth")):
            return ProviderError(
                ErrorCode.QUOTA_EXCEEDED,
                f"Rapidgator refused the download because of an account limit: {text[:200]}",
            )
        if any(w in lower for w in ("premium", "subscription", "upgrade")):
            return ProviderError(
                ErrorCode.CAPTCHA_REQUIRED,
                "The configured Rapidgator account's plan does not allow API downloads for "
                "this file. " + FREE_WORKFLOW_MESSAGE,
            )
        if status in (401, 403, 406, 423) or "denied" in lower or "password" in lower:
            return ProviderError(
                ErrorCode.AUTH_FAILED,
                "Rapidgator rejected the configured account"
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

    def _credentials(self, ctx: ProviderContext) -> dict[str, str]:
        creds = ctx.credentials or {}
        if not creds.get("login") or not creds.get("password"):
            raise ProviderError(ErrorCode.CAPTCHA_REQUIRED, FREE_WORKFLOW_MESSAGE)
        return creds

    async def _login(self, ctx: ProviderContext, *, force: bool = False) -> tuple[str, dict]:
        creds = self._credentials(ctx)
        if (
            not force
            and self._token
            and self._token[2] == creds["login"]
            and time.monotonic() - self._token[1] < TOKEN_TTL
        ):
            return self._token[0], {}
        params = {"login": creds["login"], "password": creds["password"]}
        if creds.get("code"):
            params["code"] = creds["code"]
        response = await self._call(ctx, "user/login", params)
        token = response.get("token")
        if not isinstance(token, str) or not token:
            raise ProviderError(ErrorCode.AUTH_FAILED, "Rapidgator did not return a session for this account.")
        self._token = (token, time.monotonic(), creds["login"])
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
        self._token = None
        token, _ = await self._login(ctx, force=True)
        return await self._call(ctx, path, {**params, "token": token})

    # -- adapter interface -----------------------------------------------------

    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        file_id, url_name = self._parse(url)
        creds = ctx.credentials or {}
        if not creds.get("login") or not creds.get("password"):
            return LinkInfo(
                url=url,
                provider=self.id,
                supported=False,
                filename=url_name,
                restriction=ErrorCode.CAPTCHA_REQUIRED,
                message=FREE_WORKFLOW_MESSAGE,
            )
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
        self._credentials(ctx)
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
            raise ProviderError(
                ErrorCode.CAPTCHA_REQUIRED,
                "Rapidgator did not issue an API download link for this file with the "
                "configured account. " + FREE_WORKFLOW_MESSAGE,
            )
        size = str(file.get("size", ""))
        md5 = str(file.get("hash") or "").lower()
        return ResolvedDownload(
            url=download_url,
            filename=str(file.get("name") or url_name),
            size=int(size) if size.isdigit() else None,
            checksum=("md5", md5) if re.fullmatch(r"[0-9a-f]{32}", md5) else None,
            identity=f"rapidgator:{file_id}:{md5 or size}",
        )

    async def authenticate(self, ctx: ProviderContext) -> HealthReport:
        creds = ctx.credentials or {}
        if not creds.get("login") or not creds.get("password"):
            return HealthReport(
                ok=False,
                message="No account configured. Rapidgator links cannot be downloaded: the "
                "free flow needs an interactive CAPTCHA that FETCH does not solve.",
            )
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
            "refuses API downloads for free accounts; such links will be reported as "
            "'CAPTCHA required'.",
            account=account,
        )
