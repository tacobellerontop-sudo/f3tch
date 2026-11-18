"""Guarded outbound HTTP client shared by the engine and all providers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from .. import __version__
from ..providers.base import ErrorCode, ProviderError
from ..security.urlguard import GuardedResolver, UnsafeUrlError, UrlGuard

USER_AGENT = f"FETCH/{__version__} (self-hosted download manager)"
MAX_REDIRECTS = 10
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_SENSITIVE_HEADERS = {"authorization", "cookie", "x-goog-api-key", "proxy-authorization"}


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


class HttpClient:
    """aiohttp wrapper that validates every hop against the SSRF guard."""

    def __init__(self, guard: UrlGuard) -> None:
        self.guard = guard
        self._session: aiohttp.ClientSession | None = None
        # aiohttp trace hooks, e.g. the byte meter of the network speed view.
        self.trace_configs: list[aiohttp.TraceConfig] = []

    async def start(self) -> None:
        if self._session is None:
            connector = aiohttp.TCPConnector(**self._connector_options())
            self._session = aiohttp.ClientSession(connector=connector, **self._session_options())

    def _connector_options(self) -> dict[str, Any]:
        """Keyword arguments of the TCP connector (subclasses bind it to an adapter)."""
        return {"resolver": GuardedResolver(self.guard), "limit": 64, "ttl_dns_cache": 60}

    def _session_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "timeout": aiohttp.ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=90),
            "headers": {"User-Agent": USER_AGENT},
            "auto_decompress": False,
            "cookie_jar": aiohttp.DummyCookieJar(),
        }
        if self.trace_configs:
            options["trace_configs"] = list(self.trace_configs)
        return options

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @asynccontextmanager
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
        **kwargs: Any,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """Issue a request, following redirects manually so each is validated."""
        if self._session is None:
            await self.start()
        assert self._session is not None
        headers = dict(headers or {})
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            try:
                self.guard.check_url(current)
            except UnsafeUrlError as exc:
                raise ProviderError(ErrorCode.UNSAFE_URL, str(exc)) from exc
            try:
                response = await self._session.request(
                    method, current, headers=headers, allow_redirects=False, **kwargs
                )
            except aiohttp.ClientConnectorError as exc:
                detail = str(exc.os_error) if exc.os_error else str(exc)
                if "private or local address" in detail:
                    raise ProviderError(
                        ErrorCode.UNSAFE_URL,
                        "Links to private or local network addresses are not allowed.",
                    ) from exc
                raise ProviderError(
                    ErrorCode.NETWORK, f"Could not connect to {urlsplit(current).hostname}.",
                    retryable=True,
                ) from exc
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise ProviderError(
                    ErrorCode.NETWORK,
                    f"Network error while contacting {urlsplit(current).hostname}: "
                    f"{type(exc).__name__}",
                    retryable=True,
                ) from exc
            if follow_redirects and response.status in _REDIRECT_CODES:
                location = response.headers.get("Location")
                response.release()
                if not location:
                    raise ProviderError(ErrorCode.PROTOCOL, "Redirect without a location.")
                target = urljoin(current, location)
                if _origin(target) != _origin(current):
                    headers = {
                        k: v for k, v in headers.items() if k.lower() not in _SENSITIVE_HEADERS
                    }
                if response.status == 303 and method not in ("GET", "HEAD"):
                    method = "GET"
                    kwargs.pop("data", None)
                    kwargs.pop("json", None)
                current = target
                continue
            try:
                yield response
            finally:
                response.release()
            return
        raise ProviderError(ErrorCode.PROTOCOL, "Too many redirects.")

    async def get_json(
        self, method: str, url: str, *, max_bytes: int = 2 * 1024 * 1024, **kwargs: Any
    ) -> tuple[int, Any]:
        """Small JSON API call. Returns ``(status, parsed-or-None)``."""
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        headers.update(kwargs.pop("headers", None) or {})
        timeout = aiohttp.ClientTimeout(total=45)
        async with self.request(method, url, headers=headers, timeout=timeout, **kwargs) as resp:
            try:
                chunks: list[bytes] = []
                received = 0
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    chunks.append(chunk)
                    received += len(chunk)
                    if received >= max_bytes:
                        break
                body = b"".join(chunks)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise ProviderError(
                    ErrorCode.NETWORK, "The provider did not answer in time.", retryable=True
                ) from exc
            try:
                return resp.status, json.loads(body.decode("utf-8", "replace"))
            except ValueError:
                return resp.status, None
