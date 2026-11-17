"""Google Drive through the official Drive API v3 (authorized access only).

FETCH does not scrape Drive's web download pages. You supply
either an API key (enough for files shared as "anyone with the link") or a
service-account key (for files shared with that service account).
"""

from __future__ import annotations

import base64
import json
import re
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .base import (
    AuthMode,
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

API = "https://www.googleapis.com/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_HOST = re.compile(r"^https?://(?:drive|docs)\.google\.com/", re.I)
_FILE_PATH = re.compile(r"^https?://drive\.google\.com/file/(?:u/\d+/)?d/([\w-]{10,})", re.I)
_FILE_QUERY = re.compile(
    r"^https?://drive\.google\.com/(?:u/\d+/)?(?:open|uc|thumbnail)\?(?:.*&)?id=([\w-]{10,})", re.I
)
_FOLDER = re.compile(r"^https?://drive\.google\.com/(?:drive/)?(?:u/\d+/)?folders?/", re.I)
_DOCS = re.compile(r"^https?://docs\.google\.com/(document|spreadsheets|presentation|forms)/", re.I)
_FIELDS = "id,name,size,mimeType,md5Checksum,capabilities/canDownload"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class GoogleDriveProvider(ProviderAdapter):
    id = "gdrive"
    name = "Google Drive"
    description = "Google Drive file links, through the official Drive API."
    homepage = "https://drive.google.com"
    priority = 40
    capabilities = Capabilities(
        anonymous=False, credentials=True, requires_credentials=True, checksum=True
    )
    auth_modes = (AuthMode.ACCOUNT,)
    credential_fields = (
        CredentialField(
            "api_key",
            "API key",
            help="A Google Cloud API key with the Drive API enabled. Sufficient for files "
            "shared as 'Anyone with the link'.",
        ),
        CredentialField(
            "service_account_json",
            "Service account key (JSON)",
            kind="textarea",
            help="Optional. The JSON key of a service account. Files and shared drives "
            "shared with the service account's e-mail address become downloadable.",
        ),
    )
    notes = (
        "Requires an API key or a service-account key created in your own Google Cloud "
        "project (see docs/PROVIDERS.md). Without one, Drive links are rejected.",
        "Folder links and native Google Docs/Sheets/Slides documents are not supported.",
        "Google's own download quotas and abuse flags apply and are reported as-is.",
    )
    verification = (
        "Implemented against the documented Drive API v3. Request construction, error "
        "mapping and service-account token signing are covered by automated tests against "
        "a local emulation. Not exercised against Google's live service by the tests."
    )

    def __init__(self, api_url: str = API, token_url: str = TOKEN_URL) -> None:
        self.api_url = api_url
        self.token_url = token_url
        self._token: tuple[str, float, str] | None = None  # token, expiry, key fingerprint

    def can_handle(self, url: str) -> bool:
        return bool(_HOST.match(url))

    def _file_id(self, url: str) -> str:
        if _FOLDER.match(url):
            raise ProviderError(
                ErrorCode.UNSUPPORTED,
                "Google Drive folder links are not supported. Add links to the individual files.",
            )
        if _DOCS.match(url):
            raise ProviderError(
                ErrorCode.UNSUPPORTED,
                "Native Google Docs, Sheets and Slides documents cannot be downloaded as "
                "files. Export the document in Google Drive and share the exported file.",
            )
        match = _FILE_PATH.match(url) or _FILE_QUERY.match(url)
        if not match:
            raise ProviderError(ErrorCode.UNSUPPORTED, "This is not a Google Drive file link.")
        return match.group(1)

    # -- authorization ---------------------------------------------------------

    async def _service_token(self, ctx: ProviderContext, raw_json: str) -> str:
        fingerprint = str(hash(raw_json))
        if self._token and self._token[1] - 60 > time.time() and self._token[2] == fingerprint:
            return self._token[0]
        try:
            info = json.loads(raw_json)
            email, pem = info["client_email"], info["private_key"]
            key = serialization.load_pem_private_key(pem.encode(), password=None)
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(
                ErrorCode.AUTH_FAILED, "The stored service-account key is not valid JSON key material."
            ) from exc
        now = int(time.time())
        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64url(
            json.dumps(
                {"iss": email, "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3600}
            ).encode()
        )
        signing_input = f"{header}.{claims}".encode()
        try:
            signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr,call-arg]
        except (TypeError, AttributeError, ValueError) as exc:
            raise ProviderError(
                ErrorCode.AUTH_FAILED, "The service-account key must be an RSA private key."
            ) from exc
        assertion = f"{header}.{claims}.{_b64url(signature)}"
        status, payload = await ctx.http.get_json(
            "POST",
            self.token_url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        if status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
            raise ProviderError(
                ErrorCode.AUTH_FAILED, "Google rejected the service-account key."
            )
        self._token = (
            payload["access_token"],
            time.time() + int(payload.get("expires_in", 3600)),
            fingerprint,
        )
        return self._token[0]

    async def _auth_headers(self, ctx: ProviderContext) -> dict[str, str]:
        creds = ctx.credentials or {}
        if creds.get("service_account_json"):
            token = await self._service_token(ctx, creds["service_account_json"])
            return {"Authorization": f"Bearer {token}"}
        if creds.get("api_key"):
            return {"X-Goog-Api-Key": creds["api_key"]}
        raise ProviderError(
            ErrorCode.AUTH_REQUIRED,
            "Google Drive needs an API key or service account. Add one under Providers.",
        )

    @staticmethod
    def _error(status: int, payload) -> ProviderError:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        reasons = {
            str(item.get("reason", ""))
            for item in (error.get("errors") or [] if isinstance(error, dict) else [])
            if isinstance(item, dict)
        }
        if status == 404:
            return ProviderError(
                ErrorCode.NOT_FOUND,
                "Google Drive cannot find this file, or it is not shared with the "
                "configured credentials.",
            )
        if reasons & {"downloadQuotaExceeded", "userRateLimitExceeded", "rateLimitExceeded",
                      "dailyLimitExceeded", "quotaExceeded"}:
            return ProviderError(
                ErrorCode.QUOTA_EXCEEDED,
                "Google Drive's download quota for this file or project has been exceeded. "
                "Try again later.",
            )
        if "cannotDownloadAbusiveFile" in reasons:
            return ProviderError(
                ErrorCode.UNSUPPORTED,
                "Google has flagged this file as potentially harmful; FETCH will not "
                "override that warning.",
            )
        if status == 401 or reasons & {"keyInvalid", "authError", "badRequest"} and status == 400:
            return ProviderError(ErrorCode.AUTH_FAILED, "Google rejected the configured credentials.")
        if status == 403:
            return ProviderError(
                ErrorCode.AUTH_REQUIRED,
                "Google Drive denied access to this file with the configured credentials.",
            )
        if status >= 500:
            return ProviderError(ErrorCode.UNAVAILABLE, "Google Drive is temporarily unavailable.", retryable=True)
        return ProviderError(ErrorCode.PROTOCOL, f"Google Drive returned an unexpected response ({status}).")

    async def _metadata(self, url: str, ctx: ProviderContext) -> tuple[dict, dict[str, str]]:
        file_id = self._file_id(url)
        headers = await self._auth_headers(ctx)
        status, payload = await ctx.http.get_json(
            "GET",
            f"{self.api_url}/files/{file_id}",
            params={"fields": _FIELDS, "supportsAllDrives": "true"},
            headers=headers,
        )
        if status != 200 or not isinstance(payload, dict) or "id" not in payload:
            raise self._error(status, payload)
        mime = str(payload.get("mimeType", ""))
        if mime == "application/vnd.google-apps.folder":
            raise ProviderError(ErrorCode.UNSUPPORTED, "Google Drive folder links are not supported.")
        if mime.startswith("application/vnd.google-apps."):
            raise ProviderError(
                ErrorCode.UNSUPPORTED,
                "This is a native Google document, which has no downloadable file. Export "
                "it in Google Drive first.",
            )
        if payload.get("capabilities", {}).get("canDownload") is False:
            raise ProviderError(
                ErrorCode.AUTH_REQUIRED, "The owner has disabled downloading for this file."
            )
        return payload, headers

    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        meta, _ = await self._metadata(url, ctx)
        size = str(meta.get("size", ""))
        return LinkInfo(
            url=url,
            provider=self.id,
            filename=str(meta.get("name") or ""),
            size=int(size) if size.isdigit() else None,
            resumable=True,
        )

    async def resolve(self, url: str, ctx: ProviderContext) -> ResolvedDownload:
        meta, headers = await self._metadata(url, ctx)
        size = str(meta.get("size", ""))
        md5 = str(meta.get("md5Checksum") or "").lower()

        def map_error(status: int, _headers: dict, body: str) -> ProviderError | None:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = None
            return self._error(status, payload)

        return ResolvedDownload(
            url=f"{self.api_url}/files/{meta['id']}?alt=media&supportsAllDrives=true",
            filename=str(meta.get("name") or ""),
            size=int(size) if size.isdigit() else None,
            headers=headers,
            checksum=("md5", md5) if len(md5) == 32 else None,
            identity=f"gdrive:{meta['id']}:{md5 or size}",
            error_mapper=map_error,
        )

    async def authenticate(self, ctx: ProviderContext) -> HealthReport:
        creds = ctx.credentials or {}
        if not creds:
            return HealthReport(
                ok=False, message="No credentials configured. Google Drive links cannot be downloaded yet."
            )
        try:
            headers = await self._auth_headers(ctx)
        except ProviderError as exc:
            return HealthReport(ok=False, message=exc.message)
        if creds.get("service_account_json"):
            status, payload = await ctx.http.get_json(
                "GET", f"{self.api_url}/about", params={"fields": "user/emailAddress"}, headers=headers
            )
            if status == 200 and isinstance(payload, dict):
                email = payload.get("user", {}).get("emailAddress", "")
                return HealthReport(
                    ok=True, message="Service account accepted.", account={"email": email}
                )
            return HealthReport(ok=False, message=self._error(status, payload).message)
        # An API key cannot call /about; a metadata request for a non-existent id
        # distinguishes "key works" (404) from "key rejected" (400/403).
        status, payload = await ctx.http.get_json(
            "GET", f"{self.api_url}/files/fetch-connectivity-check", params={"fields": "id"},
            headers=headers,
        )
        if status == 404:
            return HealthReport(ok=True, message="API key accepted by the Drive API.")
        return HealthReport(ok=False, message=self._error(status, payload).message)
