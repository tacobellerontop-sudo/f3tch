"""Pixeldrain, through its documented public API (https://pixeldrain.com/api)."""

from __future__ import annotations

import base64
import re

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

API = "https://pixeldrain.com/api"
_FILE = re.compile(r"^https?://(?:www\.)?pixeldrain\.(?:com|net)/(?:u|api/file)/([A-Za-z0-9_-]{4,32})", re.I)
_LIST = re.compile(r"^https?://(?:www\.)?pixeldrain\.(?:com|net)/(?:l|api/list|d)/", re.I)
_HOST = re.compile(r"^https?://(?:www\.)?pixeldrain\.(?:com|net)(?:/|$)", re.I)


class PixeldrainProvider(ProviderAdapter):
    id = "pixeldrain"
    name = "Pixeldrain"
    description = "Public Pixeldrain file links (pixeldrain.com/u/…)."
    homepage = "https://pixeldrain.com"
    priority = 30
    capabilities = Capabilities(credentials=True, checksum=True, captcha=True)
    credential_fields = (
        CredentialField(
            "api_key",
            "API key",
            help="Optional. Create one under pixeldrain.com > Account > API keys. "
            "Downloads then count against your own account's transfer limit.",
        ),
    )
    notes = (
        "Single-file links are supported. Album/list and filesystem links are reported "
        "as unsupported; submit the individual file links instead.",
        "Pixeldrain rate-limits anonymous downloads and may demand a CAPTCHA for popular "
        "files. FETCH reports that and does not attempt to get around it.",
    )
    verification = (
        "Implemented against Pixeldrain's documented API. Not exercised against the live "
        "service by the automated tests (they use no external network)."
    )

    def __init__(self, api_url: str = API) -> None:
        self.api_url = api_url

    def can_handle(self, url: str) -> bool:
        return bool(_HOST.match(url))

    def _file_id(self, url: str) -> str:
        if _LIST.match(url):
            raise ProviderError(
                ErrorCode.UNSUPPORTED,
                "Pixeldrain lists and folders are not supported. Add the individual "
                "file links (pixeldrain.com/u/…) instead.",
            )
        match = _FILE.match(url)
        if not match:
            raise ProviderError(ErrorCode.UNSUPPORTED, "This is not a Pixeldrain file link.")
        return match.group(1)

    @staticmethod
    def _auth(ctx: ProviderContext) -> dict[str, str]:
        key = (ctx.credentials or {}).get("api_key")
        if not key:
            return {}
        return {"Authorization": "Basic " + base64.b64encode(f":{key}".encode()).decode()}

    @staticmethod
    def _error(status: int, payload) -> ProviderError:
        value = (payload or {}).get("value", "") if isinstance(payload, dict) else ""
        if status == 404 or value == "not_found":
            return ProviderError(ErrorCode.NOT_FOUND, "Pixeldrain reports that this file does not exist.")
        if "captcha" in value:
            return ProviderError(
                ErrorCode.CAPTCHA_REQUIRED,
                "Pixeldrain requires a CAPTCHA for this file right now (it is rate limited). "
                "FETCH does not solve CAPTCHAs. Try again later or configure a Pixeldrain API key.",
            )
        if status == 401 or value in ("authentication_required", "authentication_failed"):
            return ProviderError(ErrorCode.AUTH_FAILED, "Pixeldrain rejected the configured API key.")
        if status == 403 or "rate_limit" in value or "transfer_limit" in value:
            return ProviderError(
                ErrorCode.QUOTA_EXCEEDED,
                "Pixeldrain's transfer limit for this file or address has been reached.",
            )
        if status == 451 or value == "unavailable_for_legal_reasons":
            return ProviderError(ErrorCode.NOT_FOUND, "Pixeldrain has blocked this file.")
        if status >= 500:
            return ProviderError(ErrorCode.UNAVAILABLE, "Pixeldrain is temporarily unavailable.", retryable=True)
        return ProviderError(ErrorCode.PROTOCOL, f"Pixeldrain returned an unexpected response ({status}).")

    async def _info(self, url: str, ctx: ProviderContext) -> dict:
        file_id = self._file_id(url)
        status, payload = await ctx.http.get_json(
            "GET", f"{self.api_url}/file/{file_id}/info", headers=self._auth(ctx)
        )
        if status != 200 or not isinstance(payload, dict) or "name" not in payload:
            raise self._error(status, payload)
        availability = str(payload.get("availability") or "")
        if "captcha" in availability:
            raise self._error(403, {"value": availability})
        return payload

    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        info = await self._info(url, ctx)
        return LinkInfo(
            url=url,
            provider=self.id,
            filename=str(info.get("name") or ""),
            size=int(info["size"]) if isinstance(info.get("size"), int) else None,
            resumable=True,
        )

    async def resolve(self, url: str, ctx: ProviderContext) -> ResolvedDownload:
        info = await self._info(url, ctx)
        digest = str(info.get("hash_sha256") or "").lower()

        def map_error(status: int, _headers: dict, body: str) -> ProviderError | None:
            import json

            try:
                payload = json.loads(body)
            except ValueError:
                payload = None
            return self._error(status, payload)

        return ResolvedDownload(
            url=f"{self.api_url}/file/{info['id']}?download",
            filename=str(info.get("name") or ""),
            size=int(info["size"]) if isinstance(info.get("size"), int) else None,
            headers=self._auth(ctx),
            checksum=("sha256", digest) if len(digest) == 64 else None,
            identity=f"pixeldrain:{info['id']}:{digest}",
            error_mapper=map_error,
        )

    async def authenticate(self, ctx: ProviderContext) -> HealthReport:
        if not (ctx.credentials or {}).get("api_key"):
            return HealthReport(ok=True, message="No API key configured; using anonymous access.")
        status, payload = await ctx.http.get_json("GET", f"{self.api_url}/user", headers=self._auth(ctx))
        if status == 200 and isinstance(payload, dict):
            return HealthReport(
                ok=True,
                message="API key accepted.",
                account={"username": payload.get("username", "")},
            )
        if status in (401, 403):
            return HealthReport(ok=False, message="Pixeldrain rejected the API key.")
        return HealthReport(ok=False, message=f"Unexpected response from Pixeldrain ({status}).")

    async def test(self, ctx: ProviderContext) -> HealthReport:
        if (ctx.credentials or {}).get("api_key"):
            return await self.authenticate(ctx)
        # Any well-formed API answer (even "not found") proves the API is reachable.
        status, payload = await ctx.http.get_json("GET", f"{self.api_url}/file/fetchcheck/info")
        if isinstance(payload, dict):
            return HealthReport(ok=True, message="Pixeldrain API is reachable (anonymous access).")
        return HealthReport(ok=False, message=f"Pixeldrain API did not answer as expected ({status}).")
