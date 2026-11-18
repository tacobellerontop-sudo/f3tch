"""Popular file hosts with public share links: Dropbox, MediaFire and WeTransfer.

Each adapter only follows the steps a browser takes for a public link. None of
them works around passwords, waits, quotas or CAPTCHAs; those are reported.
"""

from __future__ import annotations

import base64
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .base import (
    AuthMode,
    Capabilities,
    ErrorCode,
    HealthReport,
    LinkInfo,
    ProviderAdapter,
    ProviderContext,
    ProviderError,
    ResolvedDownload,
)
from .direct import filename_from_headers

_MAX_PAGE = 3 * 1024 * 1024


def _host_in(url: str, *hosts: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme in ("http", "https") and any(host == h or host.endswith("." + h) for h in hosts)


async def fetch_text(ctx: ProviderContext, url: str, **kwargs) -> tuple[int, str, str]:
    """GET a web page: ``(status, text, final url)``."""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=45)
    async with ctx.http.request("GET", url, timeout=timeout, **kwargs) as resp:
        body = bytearray()
        async for chunk in resp.content.iter_chunked(64 * 1024):
            body += chunk
            if len(body) >= _MAX_PAGE:
                break
        return resp.status, body.decode("utf-8", "replace"), str(resp.url)


async def probe(ctx: ProviderContext, url: str, headers: dict[str, str] | None = None):
    """One-byte ranged GET: ``(status, filename, size, resumable, content type)``."""
    request_headers = {**(headers or {}), "Accept-Encoding": "identity", "Range": "bytes=0-0"}
    async with ctx.http.request("GET", url, headers=request_headers) as resp:
        status, hdrs, final = resp.status, resp.headers.copy(), str(resp.url)
    size: int | None = None
    if status == 206:
        total = hdrs.get("Content-Range", "").rpartition("/")[2]
        size = int(total) if total.isdigit() else None
    elif status == 200 and hdrs.get("Content-Length", "").isdigit():
        size = int(hdrs["Content-Length"])
    resumable = status == 206 or hdrs.get("Accept-Ranges", "").lower() == "bytes"
    return status, filename_from_headers(hdrs, final), size, resumable, hdrs.get("Content-Type", "").lower()


# -- Dropbox ---------------------------------------------------------------------


class DropboxProvider(ProviderAdapter):
    id = "dropbox"
    name = "Dropbox"
    description = "Public Dropbox share links (files, and folders as a .zip)."
    homepage = "https://www.dropbox.com"
    priority = 35
    capabilities = Capabilities(resume=True)
    auth_modes = (AuthMode.ANONYMOUS,)
    notes = (
        "Shared folders are downloaded as one .zip made by Dropbox; its size is only known "
        "once it arrives.",
        "Password-protected links and links whose owner turned off downloads are not supported.",
    )
    verification = (
        "Follows Dropbox's documented dl=1 share-link behaviour. Tested against a local "
        "stand-in; not exercised against the live service."
    )

    def can_handle(self, url: str) -> bool:
        return _host_in(url, "dropbox.com", "dropboxusercontent.com", "db.tt")

    @staticmethod
    def direct_url(url: str) -> str:
        """The share link with ``dl=1``, which makes Dropbox send the file itself."""
        parts = urlsplit(url)
        if (parts.hostname or "").endswith("dropboxusercontent.com"):
            return url
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in ("dl", "raw")]
        query.append(("dl", "1"))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))

    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        from ..engine.transfer import http_error

        status, name, size, resumable, ctype = await probe(ctx, self.direct_url(url))
        if status in (404, 410) or (status in (200, 206) and ctype.startswith("text/html")):
            raise ProviderError(
                ErrorCode.NOT_FOUND,
                "Dropbox did not hand out a file for this link. It may have been deleted, "
                "need a password, or have downloads turned off.",
            )
        if status not in (200, 206):
            raise http_error(status, {}, "Dropbox")
        return LinkInfo(url=url, provider=self.id, filename=name, size=size, resumable=resumable)

    async def resolve(self, url: str, ctx: ProviderContext) -> ResolvedDownload:
        info = await self.inspect(url, ctx)
        return ResolvedDownload(
            url=self.direct_url(url), filename=info.filename, size=info.size,
            resumable=bool(info.resumable) and info.size is not None,
        )

    async def test(self, ctx: ProviderContext) -> HealthReport:
        status, _text, _final = await fetch_text(ctx, "https://www.dropbox.com/robots.txt")
        return HealthReport(ok=status < 500, message=f"Dropbox answered ({status}).")


# -- MediaFire -------------------------------------------------------------------

_MF_KEY = re.compile(
    r"mediafire\.com/(?:file(?:_premium)?/|view/|download/|download\.php\?|\?)([a-z0-9]{6,20})", re.I
)
_MF_BUTTON = re.compile(r'<a[^>]+id="downloadButton"[^>]*>', re.I)
_MF_DIRECT = re.compile(r'https?://download\d*\.mediafire\.com/[^"\'<>\s]+', re.I)


class MediaFireProvider(ProviderAdapter):
    id = "mediafire"
    name = "MediaFire"
    description = "Public MediaFire file links (mediafire.com/file/…)."
    homepage = "https://www.mediafire.com"
    priority = 35
    capabilities = Capabilities(resume=True, checksum=True)
    auth_modes = (AuthMode.ANONYMOUS,)
    notes = (
        "Single-file links are supported. Folder links are not; add the file links instead.",
        "Password-protected files are reported, not unlocked.",
    )
    verification = (
        "Uses MediaFire's public file/get_info API and the download button of the file "
        "page. Tested against a local stand-in; not exercised against the live service."
    )

    def __init__(self, site: str = "https://www.mediafire.com") -> None:
        self.site = site.rstrip("/")

    def can_handle(self, url: str) -> bool:
        return _host_in(url, "mediafire.com")

    def _key(self, url: str) -> str:
        if "/folder/" in url:
            raise ProviderError(
                ErrorCode.UNSUPPORTED,
                "MediaFire folders are not supported. Add the individual file links instead.",
            )
        match = _MF_KEY.search(url)
        if not match:
            raise ProviderError(ErrorCode.UNSUPPORTED, "This is not a MediaFire file link.")
        return match.group(1)

    async def _info(self, key: str, ctx: ProviderContext) -> dict:
        status, payload = await ctx.http.get_json(
            "GET", f"{self.site}/api/1.5/file/get_info.php?quick_key={key}&response_format=json"
        )
        response = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(response, dict):
            if status >= 500:
                raise ProviderError(ErrorCode.UNAVAILABLE, "MediaFire is temporarily unavailable.", retryable=True)
            raise ProviderError(ErrorCode.PROTOCOL, f"MediaFire returned an unexpected response ({status}).")
        info = response.get("file_info")
        if response.get("result") != "Success" or not isinstance(info, dict):
            raise ProviderError(ErrorCode.NOT_FOUND, "MediaFire reports that this file does not exist.")
        if str(info.get("password_protected", "no")).lower() == "yes":
            raise ProviderError(
                ErrorCode.AUTH_REQUIRED, "This MediaFire file is password-protected; FETCH cannot open it."
            )
        return info

    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        info = await self._info(self._key(url), ctx)
        size = str(info.get("size") or "")
        return LinkInfo(
            url=url, provider=self.id, filename=str(info.get("filename") or ""),
            size=int(size) if size.isdigit() else None, resumable=True,
        )

    @staticmethod
    def find_download_url(page: str) -> str | None:
        """The address behind the page's download button (plain or scrambled)."""
        button = _MF_BUTTON.search(page)
        if button:
            tag = button.group(0)
            scrambled = re.search(r'data-scrambled-url="([^"]+)"', tag)
            if scrambled:
                try:
                    decoded = base64.b64decode(scrambled.group(1)).decode("utf-8")
                except ValueError:
                    decoded = ""
                if _MF_DIRECT.match(decoded):
                    return decoded
            href = re.search(r'href="([^"]+)"', tag)
            if href and _MF_DIRECT.match(html.unescape(href.group(1))):
                return html.unescape(href.group(1))
        direct = _MF_DIRECT.search(page)
        return html.unescape(direct.group(0)) if direct else None

    async def resolve(self, url: str, ctx: ProviderContext) -> ResolvedDownload:
        key = self._key(url)
        info = await self._info(key, ctx)
        status, page, _final = await fetch_text(ctx, f"{self.site}/file/{key}")
        if status != 200:
            raise ProviderError(ErrorCode.UNAVAILABLE, f"MediaFire's file page answered {status}.", retryable=True)
        target = self.find_download_url(page)
        if not target:
            if "captcha" in page.lower():
                raise ProviderError(
                    ErrorCode.CAPTCHA_REQUIRED,
                    "MediaFire is asking for a CAPTCHA. Open the link in your browser once, then retry.",
                    retryable=True,
                )
            raise ProviderError(ErrorCode.PROTOCOL, "MediaFire's page had no download button FETCH recognises.")
        digest = str(info.get("hash") or "").lower()
        size = str(info.get("size") or "")
        return ResolvedDownload(
            url=target,
            filename=str(info.get("filename") or ""),
            size=int(size) if size.isdigit() else None,
            checksum=("sha256", digest) if re.fullmatch(r"[0-9a-f]{64}", digest) else None,
            identity=f"mediafire:{key}:{digest}",
        )

    async def test(self, ctx: ProviderContext) -> HealthReport:
        status, payload = await ctx.http.get_json(
            "GET", f"{self.site}/api/1.5/file/get_info.php?quick_key=fetchcheck&response_format=json"
        )
        if isinstance(payload, dict):
            return HealthReport(ok=True, message="MediaFire's API is reachable.")
        return HealthReport(ok=False, message=f"MediaFire's API did not answer as expected ({status}).")


# -- WeTransfer ------------------------------------------------------------------

_WT_PATH = re.compile(r"/downloads/([0-9a-f]{10,})/(?:([0-9a-f]{10,})/)?([0-9a-f]{4,})", re.I)


class WeTransferProvider(ProviderAdapter):
    id = "wetransfer"
    name = "WeTransfer"
    description = "WeTransfer download links (wetransfer.com/downloads/… and we.tl/…)."
    homepage = "https://wetransfer.com"
    priority = 35
    capabilities = Capabilities(resume=True)
    auth_modes = (AuthMode.ANONYMOUS,)
    notes = (
        "A transfer with several files is downloaded as one .zip made by WeTransfer.",
        "Expired and password-protected transfers are reported, not unlocked.",
    )
    verification = (
        "Uses the same download request as WeTransfer's web page. Tested against a local "
        "stand-in; not exercised against the live service."
    )

    def __init__(self, site: str = "https://wetransfer.com") -> None:
        self.site = site.rstrip("/")

    def can_handle(self, url: str) -> bool:
        return _host_in(url, "wetransfer.com", "we.tl")

    async def _ids(self, url: str, ctx: ProviderContext) -> tuple[str, str | None, str, str]:
        """(transfer id, recipient id, security hash, csrf token) for a link."""
        status, page, final = await fetch_text(ctx, url)
        match = _WT_PATH.search(urlsplit(final).path) or _WT_PATH.search(urlsplit(url).path)
        if status in (404, 410) or not match:
            raise ProviderError(ErrorCode.NOT_FOUND, "WeTransfer does not know this transfer (it may have expired).")
        token = re.search(r'name="csrf-token"\s+content="([^"]+)"', page)
        return match.group(1), match.group(2), match.group(3), token.group(1) if token else ""

    async def _direct_link(self, url: str, ctx: ProviderContext) -> str:
        transfer_id, recipient, security_hash, csrf = await self._ids(url, ctx)
        body: dict[str, str] = {"security_hash": security_hash, "intent": "entire_transfer"}
        if recipient:
            body["recipient_id"] = recipient
        headers = {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        status, payload = await ctx.http.get_json(
            "POST", f"{self.site}/api/v4/transfers/{transfer_id}/download", json=body, headers=headers
        )
        link = payload.get("direct_link") if isinstance(payload, dict) else None
        if status == 200 and isinstance(link, str) and link.startswith("http"):
            return link
        message = str((payload or {}).get("message", "")) if isinstance(payload, dict) else ""
        if status in (401, 403) and "password" in message.lower():
            raise ProviderError(ErrorCode.AUTH_REQUIRED, "This transfer is password-protected; FETCH cannot open it.")
        if status in (404, 410) or "expired" in message.lower():
            raise ProviderError(ErrorCode.NOT_FOUND, "This WeTransfer transfer has expired or was deleted.")
        if status >= 500:
            raise ProviderError(ErrorCode.UNAVAILABLE, "WeTransfer is temporarily unavailable.", retryable=True)
        raise ProviderError(ErrorCode.PROTOCOL, f"WeTransfer refused the download request ({status}).")

    async def _describe(self, url: str, ctx: ProviderContext) -> tuple[str, LinkInfo]:
        link = await self._direct_link(url, ctx)
        status, name, size, resumable, _ctype = await probe(ctx, link)
        if status not in (200, 206):
            from ..engine.transfer import http_error

            raise http_error(status, {}, "WeTransfer")
        return link, LinkInfo(url=url, provider=self.id, filename=name, size=size, resumable=resumable)

    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        return (await self._describe(url, ctx))[1]

    async def resolve(self, url: str, ctx: ProviderContext) -> ResolvedDownload:
        link, info = await self._describe(url, ctx)
        return ResolvedDownload(
            url=link, filename=info.filename, size=info.size,
            resumable=bool(info.resumable) and info.size is not None,
        )

    async def test(self, ctx: ProviderContext) -> HealthReport:
        status, _text, _final = await fetch_text(ctx, f"{self.site}/")
        return HealthReport(ok=status < 500, message=f"WeTransfer answered ({status}).")
