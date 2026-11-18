"""Transfer back-ends: native async HTTP and aria2.

A :class:`Transfer` is created by the download manager for one job attempt and
handed to the provider adapter. It moves bytes into the job's temporary file,
reports real progress, supports resume, and enforces bandwidth and size limits.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from ..providers.base import ErrorCode, ProviderError, ResolvedDownload
from .aria2 import Aria2Error, Aria2Service
from .http import HttpClient
from .limiter import RateLimiter
from .segmented import MIN_SEGMENTED_SIZE, RangeNotHonoured, SegmentedDownload

log = logging.getLogger("fetch.transfer")

CHUNK_SIZE = 256 * 1024


def _strong_validator(headers) -> str | None:
    """A value usable with ``If-Range``: a strong ETag or Last-Modified."""
    etag = headers.get("ETag")
    if etag and not etag.startswith("W/"):
        return etag
    return headers.get("Last-Modified")


def _content_range(headers) -> tuple[int | None, int | None]:
    """Parse ``Content-Range: bytes START-END/TOTAL`` -> ``(start, total)``."""
    value = headers.get("Content-Range", "")
    if not value.lower().startswith("bytes"):
        return None, None
    span, _, total = value[5:].strip().partition("/")
    start_text = span.split("-")[0].strip()
    start = int(start_text) if start_text.isdigit() else None
    return start, int(total) if total.strip().isdigit() else None


def http_error(status: int, headers, host: str) -> ProviderError:
    """Translate a file server's HTTP status into a provider error."""
    if status in (404, 410):
        return ProviderError(ErrorCode.NOT_FOUND, "The file was not found (it may have been removed).")
    if status in (401, 407):
        return ProviderError(ErrorCode.AUTH_REQUIRED, f"{host} requires authentication for this link.")
    if status == 403:
        return ProviderError(ErrorCode.AUTH_REQUIRED, f"{host} refused access to this link (403).")
    if status in (429, 503):
        retry_after = headers.get("Retry-After", "")
        if retry_after.isdigit() and 0 < int(retry_after) <= 6 * 3600:
            return ProviderError(
                ErrorCode.WAIT_REQUIRED,
                f"{host} asked FETCH to wait before retrying.",
                wait_seconds=int(retry_after),
            )
        code = ErrorCode.RATE_LIMITED if status == 429 else ErrorCode.UNAVAILABLE
        return ProviderError(code, f"{host} is temporarily refusing downloads ({status}).", retryable=True)
    if status >= 500:
        return ProviderError(ErrorCode.UNAVAILABLE, f"{host} returned a server error ({status}).", retryable=True)
    return ProviderError(ErrorCode.PROTOCOL, f"{host} returned an unexpected response ({status}).")


class Transfer:
    def __init__(
        self,
        *,
        job_id: int,
        destination: Path,
        http: HttpClient,
        limiter: RateLimiter,
        on_progress: Callable[[int, int | None], None],
        check_size: Callable[[int], None],
        aria2: Aria2Service | None = None,
        aria2_min_size: int = 16 * 1024 * 1024,
        aria2_connections: int = 4,
        connections: int = 1,
    ) -> None:
        self.job_id = job_id
        self.destination = destination
        self.http = http
        self.limiter = limiter
        self._on_progress = on_progress
        self._check_size = check_size
        self._aria2 = aria2
        self._aria2_min_size = aria2_min_size
        self._aria2_connections = aria2_connections
        self._connections = max(1, connections)
        #: Optional ``segment index -> HttpClient`` for the segmented engine.
        self.client_for: Callable[[int], HttpClient] | None = None
        self.engine = ""

    # -- helpers ---------------------------------------------------------------

    @property
    def meta_path(self) -> Path:
        return self.destination.with_name(self.destination.name + ".meta")

    @property
    def aria2_control(self) -> Path:
        return self.destination.with_name(self.destination.name + ".aria2")

    @property
    def segments_path(self) -> Path:
        return self.destination.with_name(self.destination.name + ".segments")

    def report(self, downloaded: int, total: int | None) -> None:
        self._check_size(max(downloaded, total or 0))
        self._on_progress(downloaded, total)

    def _read_meta(self) -> dict:
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def discard_partial(self) -> None:
        for path in (self.destination, self.meta_path, self.aria2_control, self.segments_path):
            with contextlib.suppress(OSError):
                path.unlink()

    # -- entry point -----------------------------------------------------------

    def _connections_for(self, resolved: ResolvedDownload) -> int:
        limit = self._connections
        if resolved.max_connections:
            limit = min(limit, resolved.max_connections)
        return limit

    async def fetch(self, resolved: ResolvedDownload) -> None:
        has_partial = self.destination.exists() and self.destination.stat().st_size > 0
        has_control = self.aria2_control.exists()
        has_segments = self.segments_path.exists()
        multi = (
            resolved.allow_aria2
            and resolved.transform is None
            and resolved.resumable
            and resolved.size is not None
        )
        aria2_ok = (
            multi
            and self._aria2 is not None
            and self._aria2.available
            and resolved.size >= self._aria2_min_size
            and not has_segments
            and (has_control or not has_partial)
        )
        if aria2_ok:
            try:
                await self._fetch_aria2(resolved)
                return
            except Aria2Error as exc:
                # aria2's errors are coarse; redo the request natively so the
                # user gets a precise reason (or simply a working download).
                log.warning(
                    "aria2 transfer failed, falling back to the native engine",
                    extra={"job_id": self.job_id, "reason": str(exc)},
                )
                self.discard_partial()
                has_partial = has_segments = False
        elif has_control:
            # A segmented aria2 partial cannot be continued by another engine.
            self.discard_partial()
            has_partial = has_segments = False

        connections = self._connections_for(resolved)
        segmented_ok = (
            multi
            and connections > 1
            and resolved.size >= MIN_SEGMENTED_SIZE
            and (has_segments or not has_partial)
        )
        if segmented_ok:
            self.engine = "segmented"
            try:
                await SegmentedDownload(self, resolved, connections, self.client_for).run()
                return
            except RangeNotHonoured:
                log.info(
                    "The server did not honour ranges; using one connection",
                    extra={"job_id": self.job_id},
                )
                self.discard_partial()
        elif has_segments:
            # Ranges of an earlier segmented attempt cannot be continued sequentially.
            self.discard_partial()
        await self._fetch_native(resolved)

    # -- native async HTTP -----------------------------------------------------

    async def _fetch_native(self, resolved: ResolvedDownload) -> None:
        self.engine = "native"
        dest = self.destination
        host = urlsplit(resolved.url).hostname or "The server"

        for attempt in range(2):
            offset = dest.stat().st_size if dest.exists() else 0
            meta = self._read_meta()
            validator: str | None = None
            if offset and resolved.resumable and attempt == 0:
                if resolved.identity:
                    if meta.get("identity") != resolved.identity:
                        offset = 0
                else:
                    validator = meta.get("validator")
                    if not validator:
                        offset = 0
            else:
                offset = 0
            offset -= offset % max(1, resolved.block_align)

            headers = {**resolved.headers, "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
                if validator:
                    headers["If-Range"] = validator

            async with self.http.request("GET", resolved.url, headers=headers) as resp:
                total: int | None
                if resp.status == 416 and offset:
                    _, total = _content_range(resp.headers)
                    if total is not None and total == offset:
                        self.report(offset, total)
                        return
                    continue  # stale partial: start over without a range
                if resp.status == 206:
                    start, total = _content_range(resp.headers)
                    if start != offset:
                        continue
                elif resp.status == 200:
                    offset = 0
                    length = resp.headers.get("Content-Length", "")
                    total = int(length) if length.isdigit() else resolved.size
                else:
                    body = ""
                    with contextlib.suppress(Exception):
                        body = (await resp.content.read(4096)).decode("utf-8", "replace")
                    mapped = (
                        resolved.error_mapper(resp.status, dict(resp.headers), body)
                        if resolved.error_mapper
                        else None
                    )
                    raise mapped or http_error(resp.status, resp.headers, host)

                self._check_size(total or 0)
                self.meta_path.write_text(
                    json.dumps(
                        {
                            "identity": resolved.identity,
                            "validator": _strong_validator(resp.headers),
                            "total": total,
                        }
                    ),
                    encoding="utf-8",
                )
                await self._stream(resp, dest, offset, total, resolved)
                return
        raise ProviderError(
            ErrorCode.PROTOCOL, "The server did not honour the resume request.", retryable=True
        )

    async def _stream(
        self,
        resp: aiohttp.ClientResponse,
        dest: Path,
        offset: int,
        total: int | None,
        resolved: ResolvedDownload,
    ) -> None:
        loop = asyncio.get_running_loop()
        handle = await loop.run_in_executor(None, lambda: open(dest, "r+b" if offset else "wb"))
        try:
            if offset:
                await loop.run_in_executor(None, handle.seek, offset)
                await loop.run_in_executor(None, handle.truncate)
            self.report(offset, total)
            async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                await self.limiter.consume(len(chunk))
                data = resolved.transform(offset, chunk) if resolved.transform else chunk
                await loop.run_in_executor(None, handle.write, data)
                offset += len(chunk)
                self.report(offset, total)
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as exc:
            raise ProviderError(
                ErrorCode.NETWORK,
                f"The connection was interrupted ({type(exc).__name__}).",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise ProviderError("storage_error", f"Could not write to disk: {exc.strerror}") from exc
        finally:
            await asyncio.shield(loop.run_in_executor(None, handle.close))
        if total is not None and offset != total:
            raise ProviderError(
                ErrorCode.NETWORK,
                "The connection closed before the whole file was received.",
                retryable=True,
            )

    # -- aria2 -----------------------------------------------------------------

    async def _fetch_aria2(self, resolved: ResolvedDownload) -> None:
        assert self._aria2 is not None
        self.engine = "aria2"
        self._check_size(resolved.size or 0)
        gid = await self._aria2.add(
            resolved.url,
            directory=self.destination.parent,
            filename=self.destination.name,
            headers=resolved.headers,
            connections=self._aria2_connections,
            speed_limit=self.limiter.rate,
        )
        try:
            while True:
                info = await self._aria2.status(gid)
                done = int(info.get("completedLength") or 0)
                total = int(info.get("totalLength") or 0) or resolved.size
                state = info.get("status")
                if state == "error":
                    raise Aria2Error(info.get("errorMessage") or f"code {info.get('errorCode')}")
                if state == "removed":
                    raise Aria2Error("The transfer was removed from aria2.")
                self.report(done, total)
                if state == "complete":
                    return
                await asyncio.sleep(0.5)
        except BaseException:
            # Pause, cancel, shutdown or failure: stop aria2 but keep its
            # control file so the download can resume later.
            await asyncio.shield(self._aria2.remove(gid))
            raise
        finally:
            await asyncio.shield(self._aria2.forget(gid))
