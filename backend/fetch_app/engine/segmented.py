"""Native multi-connection (segmented) HTTP downloads.

The file is split into byte ranges that are fetched over several parallel
connections and written straight into place in one pre-sized file. When a
connection finishes its range early it takes over half of the largest range
still in flight ("work stealing"), so a slow connection never holds the whole
download back. Progress of every range is saved next to the file, so a paused,
interrupted or failed download continues where each range stopped.

This engine needs no external program. It is used when the server supports
byte ranges and reports the size, the provider allows several connections,
and aria2 is not handling the job.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import aiohttp

from ..providers.base import ErrorCode, ProviderError, ResolvedDownload

if TYPE_CHECKING:  # pragma: no cover
    from .http import HttpClient
    from .transfer import Transfer

log = logging.getLogger("fetch.segmented")

CHUNK_SIZE = 256 * 1024
#: Never split a range below this size: tiny ranges cost more than they gain.
MIN_SPLIT = 1024 * 1024
#: Files smaller than this are fetched over one connection.
MIN_SEGMENTED_SIZE = 2 * MIN_SPLIT
#: Network errors tolerated per range before the download fails (it stays resumable).
RANGE_RETRIES = 4
STATE_VERSION = 1
SAVE_INTERVAL = 1.0


class RangeNotHonoured(Exception):
    """The server answered a range request with the whole file (or a different file)."""


@dataclass
class Segment:
    start: int
    pos: int
    end: int  # exclusive
    busy: bool = False

    @property
    def remaining(self) -> int:
        return max(0, self.end - self.pos)


def plan_segments(size: int, connections: int) -> list[Segment]:
    """Split ``size`` bytes into at most ``connections`` equal ranges."""
    count = max(1, min(connections, size // MIN_SPLIT or 1))
    step = size // count
    segments = []
    for index in range(count):
        start = index * step
        end = size if index == count - 1 else start + step
        segments.append(Segment(start, start, end))
    return segments


def _content_range(headers) -> tuple[int | None, int | None, int | None]:
    value = headers.get("Content-Range", "")
    if not value.lower().startswith("bytes"):
        return None, None, None
    span, _, total = value[5:].strip().partition("/")
    first, _, last = span.partition("-")
    def num(text: str) -> int | None:
        text = text.strip()
        return int(text) if text.isdigit() else None
    return num(first), num(last), num(total)


def _strong_validator(headers) -> str | None:
    etag = headers.get("ETag")
    if etag and not etag.startswith("W/"):
        return etag
    return headers.get("Last-Modified")


class SegmentedDownload:
    def __init__(
        self,
        transfer: Transfer,
        resolved: ResolvedDownload,
        connections: int,
        client_for: Callable[[int], HttpClient] | None = None,
    ) -> None:
        assert resolved.size is not None
        self.transfer = transfer
        self.resolved = resolved
        self.size = resolved.size
        self.connections = max(1, connections)
        self.host = urlsplit(resolved.url).hostname or "The server"
        self.segments: list[Segment] = []
        self.validator: str | None = None
        #: Picks the HTTP client for a range by its index (for example one per
        #: network adapter). Default: the transfer's shared client.
        self.client_for = client_for
        self._last_save = 0.0
        self._lock = asyncio.Lock()

    @property
    def state_path(self) -> Path:
        return self.transfer.segments_path

    # -- state -----------------------------------------------------------------

    def _load_state(self) -> bool:
        """Restore the ranges of an earlier attempt, if they belong to this file."""
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        dest = self.transfer.destination
        if (
            not isinstance(state, dict)
            or state.get("version") != STATE_VERSION
            or state.get("size") != self.size
            or not dest.exists()
            or dest.stat().st_size != self.size
        ):
            return False
        identity = self.resolved.identity
        if identity:
            if state.get("identity") != identity:
                return False
        elif not state.get("validator"):
            return False  # nothing proves the server still has the same file
        try:
            segments = [Segment(int(a), int(b), int(c)) for a, b, c in state["segments"]]
        except (KeyError, TypeError, ValueError):
            return False
        if any(not (0 <= s.start <= s.pos <= s.end <= self.size) for s in segments):
            return False
        self.segments = segments
        self.validator = state.get("validator")
        return True

    def _save_state(self) -> None:
        state = {
            "version": STATE_VERSION,
            "size": self.size,
            "identity": self.resolved.identity,
            "validator": self.validator,
            "segments": [[s.start, s.pos, s.end] for s in self.segments],
        }
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError:
            log.warning("Could not save segment state", extra={"job_id": self.transfer.job_id})

    def _maybe_save(self) -> None:
        now = time.monotonic()
        if now - self._last_save >= SAVE_INTERVAL:
            self._last_save = now
            self._save_state()

    def downloaded(self) -> int:
        return self.size - sum(s.remaining for s in self.segments)

    # -- scheduling ------------------------------------------------------------

    def _next_segment(self) -> Segment | None:
        """An idle range with work left, or half of the largest busy one."""
        for segment in self.segments:
            if not segment.busy and segment.remaining > 0:
                segment.busy = True
                return segment
        busy = [s for s in self.segments if s.busy and s.remaining >= 2 * MIN_SPLIT]
        if not busy:
            return None
        victim = max(busy, key=lambda s: s.remaining)
        middle = victim.pos + victim.remaining // 2
        stolen = Segment(middle, middle, victim.end, busy=True)
        victim.end = middle  # its connection stops when it reaches the new end
        self.segments.append(stolen)
        return stolen

    # -- transfer --------------------------------------------------------------

    async def run(self) -> None:
        dest = self.transfer.destination
        loop = asyncio.get_running_loop()
        first: Segment | None = None
        self._ready = asyncio.Event()
        if self._load_state():
            self._ready.set()
        else:
            self.segments = plan_segments(self.size, self.connections)
            self.validator = None

            def allocate() -> None:
                with open(dest, "wb") as handle:
                    handle.truncate(self.size)

            try:
                await loop.run_in_executor(None, allocate)
            except OSError as exc:
                raise ProviderError("storage_error", f"Could not create the file: {exc.strerror}") from exc
            # The first range proves that the server honours ranges and gives
            # FETCH the validator every other connection sends with If-Range.
            # The other connections start once its response headers arrived.
            first = self.segments[0]
            first.busy = True
            self._save_state()

        self.transfer.report(self.downloaded(), self.size)
        workers = [
            asyncio.create_task(
                self._worker(first if index == 0 else None), name=f"fetch-segment-{index}"
            )
            for index in range(self.connections)
        ]
        try:
            done, _pending = await asyncio.wait(workers, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                error = task.exception()
                if error is not None:
                    raise error
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            for segment in self.segments:
                segment.busy = False
            await asyncio.shield(loop.run_in_executor(None, self._save_state))

        if self.downloaded() != self.size:
            raise ProviderError(
                ErrorCode.NETWORK, "Some parts of the file were not received.", retryable=True
            )
        with contextlib.suppress(OSError):
            self.state_path.unlink()

    def _range_headers(self, segment: Segment) -> dict[str, str]:
        headers = {**self.resolved.headers, "Accept-Encoding": "identity"}
        headers["Range"] = f"bytes={segment.pos}-{segment.end - 1}"
        if self.validator:
            headers["If-Range"] = self.validator
        return headers

    def _check_response(self, resp: aiohttp.ClientResponse, segment: Segment) -> None:
        if resp.status == 206:
            start, _last, total = _content_range(resp.headers)
            if start != segment.pos or (total is not None and total != self.size):
                raise RangeNotHonoured()
            return
        if resp.status in (200, 416):
            raise RangeNotHonoured()
        raise _HttpFailure(resp.status, dict(resp.headers))

    async def _worker(self, initial: Segment | None = None) -> None:
        if initial is None:
            await self._ready.wait()
        segment = initial
        while True:
            if segment is None:
                async with self._lock:
                    segment = self._next_segment()
                if segment is None:
                    return
            failures = 0
            while segment.remaining > 0:
                try:
                    client = (
                        self.client_for(self.segments.index(segment))
                        if self.client_for else self.transfer.http
                    )
                    async with client.request(
                        "GET", self.resolved.url, headers=self._range_headers(segment)
                    ) as resp:
                        self._check_response(resp, segment)
                        if not self._ready.is_set():
                            self.validator = _strong_validator(resp.headers)
                            self._save_state()
                            self._ready.set()
                        await self._stream(resp, segment)
                    failures = 0
                except _HttpFailure as exc:
                    raise self._map_failure(exc) from None
                except ProviderError as exc:
                    failures += 1
                    if not exc.retryable or failures > RANGE_RETRIES:
                        raise
                    await asyncio.sleep(min(8.0, 0.5 * 2**failures))
            segment.busy = False
            segment = None

    def _map_failure(self, exc: _HttpFailure) -> ProviderError:
        from .transfer import http_error

        mapped = None
        if self.resolved.error_mapper:
            mapped = self.resolved.error_mapper(exc.status, exc.headers, "")
        return mapped or http_error(exc.status, exc.headers, self.host)

    async def _stream(self, resp: aiohttp.ClientResponse, segment: Segment) -> None:
        loop = asyncio.get_running_loop()
        dest = self.transfer.destination
        handle = await loop.run_in_executor(None, lambda: open(dest, "r+b"))
        try:
            await loop.run_in_executor(None, handle.seek, segment.pos)
            async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                room = segment.end - segment.pos
                if room <= 0:
                    break  # another connection took over the rest of this range
                if len(chunk) > room:
                    chunk = chunk[:room]
                await self.transfer.limiter.consume(len(chunk))
                await loop.run_in_executor(None, handle.write, chunk)
                segment.pos += len(chunk)
                self.transfer.report(self.downloaded(), self.size)
                self._maybe_save()
                if segment.pos >= segment.end:
                    break
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as exc:
            raise ProviderError(
                ErrorCode.NETWORK,
                f"A connection was interrupted ({type(exc).__name__}).",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise ProviderError("storage_error", f"Could not write to disk: {exc.strerror}") from exc
        finally:
            await asyncio.shield(loop.run_in_executor(None, handle.close))
        if segment.pos < segment.end:
            raise ProviderError(
                ErrorCode.NETWORK, "A connection closed before its part was received.", retryable=True
            )


class _HttpFailure(Exception):
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        super().__init__(status)
        self.status = status
        self.headers = headers
