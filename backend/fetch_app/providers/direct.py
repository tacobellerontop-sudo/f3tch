"""Direct HTTP/HTTPS links (the fallback provider)."""

from __future__ import annotations

import re
from email.message import Message
from urllib.parse import unquote, urlsplit

from .base import (
    Capabilities,
    ErrorCode,
    HealthReport,
    LinkInfo,
    ProviderAdapter,
    ProviderContext,
    ProviderError,
    ResolvedDownload,
)


def filename_from_headers(headers, url: str) -> str:
    """Best-effort file name: Content-Disposition first, then the URL path."""
    disposition = headers.get("Content-Disposition")
    if disposition:
        msg = Message()
        msg["Content-Disposition"] = disposition
        try:
            name = msg.get_filename()
        except Exception:
            name = None
        if name:
            return str(name)
        match = re.search(r'filename="?([^";]+)"?', disposition, re.I)
        if match:
            return match.group(1)
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


class DirectHttpProvider(ProviderAdapter):
    id = "direct"
    name = "Direct HTTP/HTTPS"
    description = "Any publicly reachable http:// or https:// file link."
    priority = 1000  # asked last: every other provider gets the first look
    capabilities = Capabilities(resume=True)
    notes = (
        "Resume works when the server supports range requests and sends an ETag or "
        "Last-Modified header.",
        "Links that need a login, cookies or a CAPTCHA in a browser are not supported.",
    )
    verification = "Verified end to end by the automated test suite against local HTTP servers."

    def can_handle(self, url: str) -> bool:
        return urlsplit(url).scheme.lower() in ("http", "https")

    async def _probe(self, url: str, ctx: ProviderContext):
        """HEAD, falling back to a one-byte ranged GET for servers without HEAD."""
        headers = {"Accept-Encoding": "identity"}
        async with ctx.http.request("HEAD", url, headers=headers) as resp:
            status, hdrs, final = resp.status, resp.headers.copy(), str(resp.url)
        if status in (403, 405, 501) or (status == 200 and "Content-Length" not in hdrs):
            async with ctx.http.request(
                "GET", url, headers={**headers, "Range": "bytes=0-0"}
            ) as resp:
                status, hdrs, final = resp.status, resp.headers.copy(), str(resp.url)
        return status, hdrs, final

    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        from ..engine.transfer import http_error

        status, headers, final = await self._probe(url, ctx)
        host = urlsplit(final).hostname or "The server"
        if status not in (200, 206):
            raise http_error(status, headers, host)

        size: int | None = None
        if status == 206:
            total = headers.get("Content-Range", "").rpartition("/")[2]
            size = int(total) if total.isdigit() else None
        elif headers.get("Content-Length", "").isdigit():
            size = int(headers["Content-Length"])
        resumable = status == 206 or headers.get("Accept-Ranges", "").lower() == "bytes"

        info = LinkInfo(
            url=url,
            provider=self.id,
            filename=filename_from_headers(headers, final),
            size=size,
            resumable=resumable,
        )
        content_type = headers.get("Content-Type", "").lower()
        if content_type.startswith("text/html") and "Content-Disposition" not in headers:
            info.restriction = "html_page"
            info.message = (
                "This link returns a web page rather than a file. If the site needs a "
                "login or shows a download button, FETCH will only save the page itself."
            )
        return info

    async def resolve(self, url: str, ctx: ProviderContext) -> ResolvedDownload:
        info = await self.inspect(url, ctx)
        return ResolvedDownload(
            url=url, filename=info.filename, size=info.size, resumable=bool(info.resumable)
        )

    async def test(self, ctx: ProviderContext) -> HealthReport:
        try:
            async with ctx.http.request("HEAD", "https://www.example.com/") as resp:
                return HealthReport(ok=resp.status < 500, message="Outbound HTTPS is working.")
        except ProviderError as exc:
            if exc.code == ErrorCode.UNSAFE_URL:
                raise
            return HealthReport(ok=False, message=f"Outbound HTTPS check failed: {exc.message}")
