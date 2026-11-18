"""FETCH's own job-management layer.

The :class:`DownloadManager` owns the queue: it admits submitted jobs, decides
what runs (concurrency, priority, order), drives each job
through its states, retries, waits out provider-imposed delays, and persists
everything so the queue survives a restart. Transfer back-ends (native HTTP,
aria2) and provider adapters are collaborators; none of them knows the queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..activity import ActivityService
from ..db import Database, utcnow
from ..events import EventBus
from ..models import (
    FINISHED_STATES,
    RUNNING_STATES,
    Download,
    DownloadState,
    FileRecord,
)
from ..providers.base import CaptchaChallenge, ErrorCode, ProviderError
from ..providers.registry import ProviderRegistry
from ..schemas import serialize_download
from ..settings_service import GB, SettingsService
from ..storage import StorageError, StorageManager, sanitize_filename
from .aria2 import Aria2Service
from .http import HttpClient
from .limiter import RateLimiter
from .transfer import Transfer

log = logging.getLogger("fetch.manager")
T = TypeVar("T")

S = DownloadState
PROGRESS_INTERVAL = 0.5
PERSIST_INTERVAL = 3.0
SPEED_WINDOW = 5.0
PAYLOAD_NAME = "payload"

_STATE_EVENTS = {
    S.DOWNLOADING.value: "download.started",
    S.PAUSED.value: "download.paused",
    S.COMPLETED.value: "download.completed",
    S.FAILED.value: "download.failed",
    S.CANCELLED.value: "download.cancelled",
}
_PROVIDER_ERROR_CODES = {
    ErrorCode.AUTH_FAILED, ErrorCode.AUTH_REQUIRED, ErrorCode.CAPTCHA_REQUIRED,
    ErrorCode.QUOTA_EXCEEDED, ErrorCode.UNAVAILABLE, ErrorCode.PROTOCOL,
    ErrorCode.RATE_LIMITED, ErrorCode.WAIT_REQUIRED, ErrorCode.ANON_UNAVAILABLE,
}
CAPTCHA_WAIT_REASON = "Waiting for you to complete the provider's CAPTCHA."


class JobHandle:
    """In-memory state of one running job."""

    def __init__(self, job_id: int, user_id: int) -> None:
        self.job_id = job_id
        self.user_id = user_id
        self.task: asyncio.Task | None = None
        self.stop_reason: str | None = None
        self.downloaded = 0
        self.total: int | None = None
        self.speed = 0.0
        self.base_seconds = 0.0  # active time accumulated in earlier sessions
        self.started = 0.0
        self.samples: deque[tuple[float, int]] = deque()
        self.last_emit = 0.0
        self.last_persist = 0.0
        self.transferring = False

    @property
    def active_seconds(self) -> float:
        if not self.started:
            return self.base_seconds
        return self.base_seconds + (time.monotonic() - self.started)

    @property
    def average(self) -> float:
        seconds = self.active_seconds
        return self.downloaded / seconds if seconds > 0.05 else 0.0


class DownloadManager:
    def __init__(
        self,
        *,
        db: Database,
        settings: SettingsService,
        storage: StorageManager,
        registry: ProviderRegistry,
        http: HttpClient,
        bus: EventBus,
        activity: ActivityService,
        aria2: Aria2Service | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.storage = storage
        self.registry = registry
        self.http = http
        self.bus = bus
        self.activity = activity
        self.aria2 = aria2
        self._active: dict[int, JobHandle] = {}
        self._limiter = RateLimiter()
        # CAPTCHA challenges waiting for their download's owner, and what the
        # owner supplied for them. Memory only: a provider's challenge does not
        # survive a restart anyway.
        self._captchas: dict[int, tuple[CaptchaChallenge, float]] = {}
        self._inputs: dict[int, dict[str, str]] = {}
        self._wake = asyncio.Event()
        # Serialises scheduling passes with pause/cancel/remove so a control
        # request can never slip between a job being picked and being tracked.
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread = 0
        self._scheduler: asyncio.Task | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._stopping = False
        self._background: set[asyncio.Task] = set()
        self.started_at: float | None = None
        registry.bind_runtime(self)

    # ------------------------------------------------------------------ helpers

    async def _db(self, fn: Callable[[Session], T]) -> T:
        def run() -> T:
            with self.db.session() as session:
                return fn(session)

        return await asyncio.to_thread(run)

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def wake(self) -> None:
        """Ask the scheduler to look at the queue. Safe from any thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        if threading.get_ident() == self._loop_thread:
            self._wake.set()
        else:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._wake.set)

    def publish_download(self, row: Download, event: str | None = None) -> None:
        """Broadcast a job's current state to the dashboard."""
        data = serialize_download(row)
        self.bus.publish(
            event or _STATE_EVENTS.get(row.state, "download.updated"),
            {"download": data},
            user_id=row.user_id,
        )

    # ---------------------------------------------------------------- lifecycle

    @property
    def running(self) -> bool:
        return self._scheduler is not None and not self._scheduler.done()

    @property
    def active_count(self) -> int:
        return len(self._active)

    def aggregate_speed(self, user_id: int | None = None) -> float:
        return sum(
            h.speed for h in self._active.values() if user_id is None or h.user_id == user_id
        )

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        self._stopping = False
        recovered = await self._db(self._recover)
        if recovered:
            log.info("Recovered interrupted downloads", extra={"count": recovered})
        self._scheduler = self._loop.create_task(self._run(), name="fetch-scheduler")
        self.started_at = time.time()
        self.wake()
        log.info("Download manager started")

    @staticmethod
    def _recover(session: Session) -> int:
        """Jobs that were mid-flight when FETCH stopped go back to the queue."""
        rows = (
            session.query(Download)
            .filter(Download.state.in_([s.value for s in RUNNING_STATES]))
            .all()
        )
        for row in rows:
            row.state = S.QUEUED.value
            row.speed_bps = 0.0
        return len(rows)

    async def stop(self) -> None:
        """Stop all workers, preserving progress so jobs resume after a restart."""
        self._stopping = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._scheduler is not None:
            self._scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._scheduler
            self._scheduler = None
        handles = list(self._active.values())
        for handle in handles:
            if handle.stop_reason is None:
                handle.stop_reason = "shutdown"
            if handle.task is not None:
                handle.task.cancel()
        tasks = [h.task for h in handles if h.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)
        log.info("Download manager stopped", extra={"interrupted": len(tasks)})

    async def restart(self) -> int:
        """Restart the workers. Running jobs are requeued and resume."""
        interrupted = len(self._active)
        await self.stop()
        await self.start()
        return interrupted

    # ---------------------------------------------------------------- scheduler

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                await self._schedule()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scheduler pass failed")
                await asyncio.sleep(2)
                self._wake.set()

    async def _schedule(self) -> None:
        async with self._lock:
            await self._schedule_locked()

    async def _schedule_locked(self) -> None:
        if self._stopping:
            return
        picked, next_wait, changed = await self._db(
            lambda s: self._pick(s, len(self._active))
        )
        for row in changed:
            self.publish_download(row)
        for row in picked:
            handle = JobHandle(row.id, row.user_id)
            handle.downloaded = row.downloaded_bytes
            handle.total = row.total_bytes
            handle.base_seconds = row.active_seconds or 0.0
            self._active[row.id] = handle
            handle.task = asyncio.get_running_loop().create_task(
                self._run_job(handle), name=f"fetch-job-{row.id}"
            )
            self.publish_download(row)
        if picked or changed:
            self.bus.publish("queue.changed", {}, audience="all")
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if next_wait is not None:
            self._timer = asyncio.get_running_loop().call_later(max(0.2, next_wait), self._wake.set)

    def _pick(
        self, session: Session, active_total: int
    ) -> tuple[list[Download], float | None, list[Download]]:
        settings = self.settings.get()
        now = utcnow()
        changed: list[Download] = []

        # Admit newly submitted jobs to the queue.
        pending = (
            session.query(Download).filter(Download.state == S.PENDING.value)
            .order_by(Download.id).all()
        )
        if pending:
            position = session.query(func.max(Download.position)).scalar() or 0
            for row in pending:
                position += 1
                row.state, row.position = S.QUEUED.value, position
                changed.append(row)

        # Jobs whose waiting period is over are eligible again.
        for row in (
            session.query(Download)
            .filter(Download.state == S.WAITING.value, Download.wait_until <= now)
            .all()
        ):
            row.state, row.wait_until, row.wait_reason = S.QUEUED.value, None, ""
            changed.append(row)

        picked: list[Download] = []
        free = settings.downloads.max_global_concurrent - active_total
        if free > 0:
            candidates = (
                session.query(Download)
                .filter(Download.state == S.QUEUED.value)
                .order_by(Download.priority.desc(), Download.position, Download.id)
                .limit(free)
                .all()
            )
            for row in candidates:
                row.state = S.INSPECTING.value
                row.error_code, row.error_message = "", ""
                picked.append(row)

        session.flush()
        changed = [row for row in changed if row not in picked]

        soonest = (
            session.query(func.min(Download.wait_until))
            .filter(Download.state == S.WAITING.value)
            .scalar()
        )
        next_wait = (soonest - now).total_seconds() if soonest else None
        return picked, next_wait, changed

    # --------------------------------------------------------------------- jobs

    async def _run_job(self, handle: JobHandle) -> None:
        job_id = handle.job_id
        try:
            await self._execute(handle)
        except asyncio.CancelledError:
            await asyncio.shield(self._on_stopped(handle, handle.stop_reason or "shutdown"))
        except ProviderError as exc:
            await asyncio.shield(self._on_error(handle, exc))
        except StorageError as exc:
            await asyncio.shield(self._on_error(handle, ProviderError("storage_error", str(exc))))
        except Exception:
            log.exception("Download worker crashed", extra={"job_id": job_id})
            await asyncio.shield(
                self._on_error(
                    handle,
                    ProviderError(
                        "internal_error",
                        "An internal error stopped this download. Details are in the "
                        "log under Settings.",
                    ),
                )
            )
        finally:
            self._active.pop(job_id, None)
            self.wake()

    async def _execute(self, handle: JobHandle) -> None:
        job_id = handle.job_id
        settings = self.settings.get()

        def load(session: Session) -> dict[str, Any]:
            row = session.get(Download, job_id)
            if row is None:
                raise ProviderError("removed", "The download no longer exists.")
            return {
                "url": row.url,
                "provider": row.provider,
                "filename": row.filename,
                "destination": row.destination,
                "folder": row.folder,
            }

        job = await self._db(load)
        provider = self.registry.get(job["provider"])
        if provider is None:
            raise ProviderError(ErrorCode.UNSUPPORTED, "The provider for this link is not installed.")
        if not await asyncio.to_thread(self.registry.is_enabled, provider.id):
            raise ProviderError(
                ErrorCode.DISABLED, f"The {provider.name} provider is switched off on the Providers page."
            )
        self._captchas.pop(job_id, None)
        user_input = self._inputs.pop(job_id, None)
        ctx = await asyncio.to_thread(
            lambda: self.registry.context(provider, job_id=job_id, user_input=user_input)
        )

        # --- Inspecting: run the provider's own workflow -----------------------
        resolved = await provider.resolve(job["url"], ctx)
        filename = sanitize_filename(resolved.filename or job["filename"], fallback=f"download-{job_id}")

        # --- Limits (always enforced here, whatever the client claimed) --------
        scope, user_id = job["destination"], handle.user_id
        room: int | None = None
        if settings.storage.max_storage_gb > 0:
            total_used = await asyncio.to_thread(self.storage.total_usage)
            room = max(0, int(settings.storage.max_storage_gb * GB) - total_used)

        def check_size(size: int) -> None:
            if room is not None and size > room:
                raise ProviderError(
                    "storage_quota_exceeded",
                    "This file does not fit in the storage limit set under Settings.",
                )

        check_size(resolved.size or 0)
        free_disk = self.storage.disk()["free"]
        if resolved.size and free_disk and resolved.size > free_disk:
            raise ProviderError("storage_error", "There is not enough free disk space for this file.")

        # One limiter for all transfers: the speed limit applies to FETCH as a whole.
        limiter = self._limiter
        limiter.set_rate(settings.downloads.speed_limit_kbps * 1024)

        temp_dir = await asyncio.to_thread(self.storage.job_temp_dir, job_id)
        payload = temp_dir / PAYLOAD_NAME
        transfer = Transfer(
            job_id=job_id,
            destination=payload,
            http=self.http,
            limiter=limiter,
            on_progress=lambda done, total: self._on_progress(handle, done, total),
            check_size=check_size,
            aria2=self.aria2 if settings.downloads.use_aria2 else None,
            aria2_min_size=settings.downloads.aria2_min_size_mb * 1024 * 1024,
            aria2_connections=settings.downloads.aria2_connections,
        )

        # --- Downloading -------------------------------------------------------
        def to_downloading(session: Session) -> Download:
            row = session.get(Download, job_id)
            row.state = S.DOWNLOADING.value
            row.filename = filename
            row.total_bytes = resolved.size
            row.started_at = row.started_at or utcnow()
            row.wait_until, row.wait_reason = None, ""
            return row

        row = await self._db(to_downloading)
        log.info("Download started", extra={"job_id": job_id, "provider": provider.id, "user_id": user_id})
        self.publish_download(row)
        handle.total = resolved.size
        handle.started = time.monotonic()
        handle.transferring = True
        try:
            await provider.download(transfer, resolved)
        finally:
            handle.transferring = False
            handle.base_seconds = handle.active_seconds
            handle.started = 0.0
            handle.speed = 0.0

        # --- Verifying ---------------------------------------------------------
        def to_verifying(session: Session) -> Download:
            row = session.get(Download, job_id)
            row.state = S.VERIFYING.value
            row.engine = transfer.engine
            row.downloaded_bytes = handle.downloaded
            row.speed_bps = 0.0
            row.avg_speed_bps = handle.average
            row.active_seconds = handle.base_seconds
            return row

        self.publish_download(await self._db(to_verifying))
        size = payload.stat().st_size
        try:
            if resolved.size is not None and size != resolved.size:
                raise ProviderError(
                    ErrorCode.VERIFY_FAILED,
                    f"The file size does not match (expected {resolved.size} bytes, got {size}).",
                    retryable=True,
                )
            checksum = ""
            if resolved.checksum:
                algorithm, expected = resolved.checksum
                actual = await asyncio.to_thread(_hash_file, payload, algorithm)
                if actual != expected.lower():
                    raise ProviderError(
                        ErrorCode.VERIFY_FAILED,
                        f"The {algorithm.upper()} checksum does not match the one published "
                        "by the provider.",
                        retryable=True,
                    )
                checksum = f"{algorithm}:{actual}"
            if resolved.verifier:
                await resolved.verifier(payload)
        except ProviderError:
            transfer.discard_partial()  # a corrupt partial must not be resumed
            raise

        # --- Finalize ----------------------------------------------------------
        final = await asyncio.to_thread(
            self.storage.finalize, payload, scope, user_id, job["folder"], filename
        )
        rel_path = self.storage.rel_to_space(scope, user_id, final)

        def complete(session: Session) -> Download:
            row = session.get(Download, job_id)
            space = user_id if scope == "private" else 0
            session.query(FileRecord).filter_by(scope=scope, space=space, rel_path=rel_path).delete()
            session.add(
                FileRecord(
                    owner_id=user_id, scope=scope, space=space, rel_path=rel_path, size=size,
                    provider=row.provider, source_url=row.url, download_id=row.id, checksum=checksum,
                )
            )
            row.state = S.COMPLETED.value
            row.filename = final.name
            row.final_path = rel_path
            row.total_bytes = row.downloaded_bytes = size
            row.finished_at = utcnow()
            row.error_code, row.error_message = "", ""
            return row

        row = await self._db(complete)
        await asyncio.to_thread(self.storage.remove_job_temp, job_id)
        await asyncio.to_thread(self.registry.mark_reachable, provider.id)
        self.publish_download(row)
        self.bus.publish("storage.changed", {"scope": scope}, user_id=user_id,
                         audience="all" if scope == "shared" else "owner")
        await asyncio.to_thread(
            self.activity.record, "download", "download.completed",
            f"Downloaded {final.name}", user_id=user_id, target=str(job_id),
            detail={"provider": provider.id, "bytes": size, "engine": transfer.engine},
        )

    # ----------------------------------------------------------------- progress

    def _on_progress(self, handle: JobHandle, downloaded: int, total: int | None) -> None:
        now = time.monotonic()
        handle.downloaded = downloaded
        if total is not None:
            handle.total = total
        samples = handle.samples
        samples.append((now, downloaded))
        while len(samples) > 2 and now - samples[0][0] > SPEED_WINDOW:
            samples.popleft()
        span = samples[-1][0] - samples[0][0]
        if span > 0.2:
            handle.speed = max(0.0, (samples[-1][1] - samples[0][1]) / span)
        if now - handle.last_emit >= PROGRESS_INTERVAL:
            handle.last_emit = now
            remaining = (handle.total - downloaded) if handle.total else None
            self.bus.publish(
                "download.progress",
                {
                    "id": handle.job_id,
                    "downloaded_bytes": downloaded,
                    "total_bytes": handle.total,
                    "speed_bps": handle.speed,
                    "avg_speed_bps": handle.average,
                    "eta_seconds": (remaining / handle.speed)
                    if remaining is not None and handle.speed > 0 else None,
                },
                user_id=handle.user_id,
            )
        if now - handle.last_persist >= PERSIST_INTERVAL:
            handle.last_persist = now
            self._spawn(self._persist_progress(handle))

    async def _persist_progress(self, handle: JobHandle) -> None:
        snapshot = (handle.downloaded, handle.total, handle.speed, handle.average, handle.active_seconds)

        def save(session: Session) -> None:
            row = session.get(Download, handle.job_id)
            if row is not None and row.state == S.DOWNLOADING.value:
                (row.downloaded_bytes, total, row.speed_bps, row.avg_speed_bps,
                 row.active_seconds) = snapshot
                if total is not None:
                    row.total_bytes = total

        with contextlib.suppress(Exception):
            await self._db(save)

    # ------------------------------------------------------------------ endings

    async def _on_stopped(self, handle: JobHandle, reason: str) -> None:
        """A running job was interrupted by pause / cancel / remove / shutdown."""
        job_id = handle.job_id
        target = {
            "pause": S.PAUSED, "cancel": S.CANCELLED, "shutdown": S.QUEUED, "requeue": S.QUEUED,
        }.get(reason)

        def save(session: Session) -> Download | None:
            row = session.get(Download, job_id)
            if row is None:
                return None
            if reason == "remove":
                session.delete(row)
                return None
            row.state = target.value  # type: ignore[union-attr]
            row.speed_bps = 0.0
            row.downloaded_bytes = handle.downloaded if target != S.CANCELLED else 0
            row.avg_speed_bps = handle.average
            row.active_seconds = handle.active_seconds
            if target == S.CANCELLED:
                row.finished_at = utcnow()
            return row

        try:
            row = await self._db(save)
            if reason in ("cancel", "remove"):
                await asyncio.to_thread(self.storage.remove_job_temp, job_id)
                self._release_provider_session(job_id)
            if row is not None:
                log.info("Download %s", target.value, extra={"job_id": job_id, "reason": reason})  # type: ignore[union-attr]
                if reason != "shutdown":
                    self.publish_download(row)
            elif reason == "remove":
                self.bus.publish("download.removed", {"id": job_id}, user_id=handle.user_id)
        except Exception:
            log.exception("Could not record the stop of a download", extra={"job_id": job_id})

    async def _on_error(self, handle: JobHandle, exc: ProviderError) -> None:
        job_id = handle.job_id
        if exc.code == "removed":
            return
        downloads = self.settings.get().downloads

        def save(session: Session) -> tuple[Download, str]:
            row = session.get(Download, job_id)
            row.speed_bps = 0.0
            row.downloaded_bytes = handle.downloaded
            row.avg_speed_bps = handle.average
            row.active_seconds = handle.active_seconds
            row.error_code, row.error_message = exc.code, exc.message
            if exc.challenge is not None:
                # The provider demands a CAPTCHA its user can complete. The job
                # waits for them; FETCH itself does nothing with the challenge.
                outcome = "captcha"
                row.state = S.WAITING.value
                row.wait_until = utcnow() + timedelta(seconds=max(1.0, exc.challenge.expires_in))
                row.wait_reason = CAPTCHA_WAIT_REASON
            elif exc.wait_seconds is not None:
                outcome = "waiting"
                row.state = S.WAITING.value
                row.wait_until = utcnow() + timedelta(seconds=max(1.0, exc.wait_seconds))
                row.wait_reason = exc.message
            elif exc.retryable and row.attempts < row.max_attempts:
                outcome = "retrying"
                row.attempts += 1
                row.state = S.WAITING.value
                row.wait_until = utcnow() + timedelta(seconds=downloads.retry_delay_seconds)
                row.wait_reason = (
                    f"Retry {row.attempts} of {row.max_attempts} after an error: {exc.message}"
                )
            else:
                outcome = "failed"
                row.state = S.FAILED.value
                row.finished_at = utcnow()
            return row, outcome

        if exc.challenge is not None:
            self._captchas[job_id] = (exc.challenge, time.time() + exc.challenge.expires_in)
        try:
            row, outcome = await self._db(save)
        except Exception:
            log.exception("Could not record a download failure", extra={"job_id": job_id})
            return
        if outcome == "failed":
            self._release_provider_session(job_id)
        self.publish_download(row)
        level = "error" if outcome == "failed" else "warning"
        provider_issue = exc.code in _PROVIDER_ERROR_CODES and row.provider != "direct"
        await asyncio.to_thread(
            self.activity.record,
            "provider" if provider_issue else "download",
            f"download.{outcome}",
            f"{row.filename or row.url}: {exc.message}",
            user_id=row.user_id, level=level, target=row.provider if provider_issue else str(job_id),
            detail={"code": exc.code, "provider": row.provider, "download_id": job_id,
                    "attempts": row.attempts},
        )
        if not provider_issue:
            return
        if exc.code == ErrorCode.AUTH_FAILED:
            await asyncio.to_thread(self.registry.mark_account, row.provider, False, exc.message)
            await asyncio.to_thread(self.registry.record_health, row.provider, False, exc.message)
        elif exc.code == ErrorCode.UNAVAILABLE:
            await asyncio.to_thread(
                lambda: self.registry.record_health(
                    row.provider, False, exc.message, unavailable=True
                )
            )

    def _release_provider_session(self, job_id: int) -> None:
        """The job ended: drop its challenge and any session a provider kept for it."""
        self._captchas.pop(job_id, None)
        self._inputs.pop(job_id, None)
        for provider in self.registry.all():
            with contextlib.suppress(Exception):
                provider.release_job(job_id)

    # ------------------------------------------------------------------ CAPTCHA

    def captcha(self, job_id: int) -> CaptchaChallenge | None:
        """The challenge waiting for this download's owner, if it is still valid."""
        entry = self._captchas.get(job_id)
        if entry is None:
            return None
        return entry[0] if entry[1] > time.time() else None

    def captcha_expires_at(self, job_id: int) -> float | None:
        entry = self._captchas.get(job_id)
        return entry[1] if entry else None

    async def submit_captcha(self, job_id: int, values: dict[str, str]) -> Download | None:
        """Hand the owner's own answer to the provider and let the job continue."""
        async with self._lock:
            if self.captcha(job_id) is None:
                return None

            def apply(row: Download) -> None:
                row.state = S.QUEUED.value
                row.wait_until, row.wait_reason = None, ""

            def run(session: Session) -> Download | None:
                row = session.get(Download, job_id)
                if (
                    row is None or row.state != S.WAITING.value
                    or row.error_code != ErrorCode.CAPTCHA_REQUIRED
                ):
                    return None
                apply(row)
                session.flush()
                return row

            row = await self._db(run)
            if row is None:
                return None
            self._inputs[job_id] = values
            self.publish_download(row)
            self.bus.publish("queue.changed", {}, audience="all")
            self.wake()
            return row

    def run_from_thread(self, coro: Any, timeout: float = 30.0) -> Any:
        """Run ``coro`` on the manager's loop from another thread and return its result."""
        loop = self._loop
        if loop is None or loop.is_closed() or threading.get_ident() == self._loop_thread:
            coro.close()
            raise RuntimeError("The download manager is not running.")
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    # ----------------------------------------------------------------- controls

    async def _stop_active(self, job_id: int, reason: str) -> bool:
        handle = self._active.get(job_id)
        if handle is None or handle.task is None:
            return False
        if handle.stop_reason is None:
            handle.stop_reason = reason
        handle.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait({handle.task}, timeout=30)
        return True

    async def _transition(
        self, job_id: int, allowed: tuple[DownloadState, ...], apply: Callable[[Download], None]
    ) -> Download | None:
        def run(session: Session) -> Download | None:
            row = session.get(Download, job_id)
            if row is None or row.state not in [s.value for s in allowed]:
                return row if row is None else _detach(row)
            apply(row)
            session.flush()
            return row

        return await self._db(run)

    async def pause(self, job_id: int) -> Download | None:
        async with self._lock:
            return await self._pause(job_id)

    async def _pause(self, job_id: int) -> Download | None:
        if not await self._stop_active(job_id, "pause"):
            def apply(row: Download) -> None:
                row.state = S.PAUSED.value
                row.wait_until, row.wait_reason = None, ""

            row = await self._transition(job_id, (S.PENDING, S.QUEUED, S.WAITING), apply)
            if row is not None and row.state == S.PAUSED.value:
                self.publish_download(row)
        self.bus.publish("queue.changed", {}, audience="all")
        self.wake()
        return await self.get(job_id)

    async def resume(self, job_id: int) -> Download | None:
        async with self._lock:
            return await self._resume(job_id)

    async def _resume(self, job_id: int) -> Download | None:
        def apply(row: Download) -> None:
            row.state = S.QUEUED.value
            row.error_code, row.error_message = "", ""

        row = await self._transition(job_id, (S.PAUSED,), apply)
        if row is not None and row.state == S.QUEUED.value:
            self.publish_download(row)
        self.bus.publish("queue.changed", {}, audience="all")
        self.wake()
        return row

    async def cancel(self, job_id: int) -> Download | None:
        async with self._lock:
            return await self._cancel(job_id)

    async def _cancel(self, job_id: int) -> Download | None:
        if not await self._stop_active(job_id, "cancel"):
            def apply(row: Download) -> None:
                row.state = S.CANCELLED.value
                row.finished_at = utcnow()
                row.wait_until, row.wait_reason = None, ""
                row.downloaded_bytes = 0

            row = await self._transition(job_id, (S.PENDING, S.QUEUED, S.WAITING, S.PAUSED), apply)
            if row is not None and row.state == S.CANCELLED.value:
                await asyncio.to_thread(self.storage.remove_job_temp, job_id)
                self._release_provider_session(job_id)
                self.publish_download(row)
        self.bus.publish("queue.changed", {}, audience="all")
        self.wake()
        return await self.get(job_id)

    async def retry(self, job_id: int) -> Download | None:
        async with self._lock:
            return await self._retry(job_id)

    async def _retry(self, job_id: int) -> Download | None:
        retry_count = self.settings.get().downloads.retry_count

        def apply(row: Download) -> None:
            row.state = S.PENDING.value
            row.attempts = 0
            row.max_attempts = retry_count
            row.error_code, row.error_message = "", ""
            row.finished_at = None
            row.wait_until, row.wait_reason = None, ""

        row = await self._transition(job_id, (S.FAILED, S.CANCELLED), apply)
        if row is not None and row.state == S.PENDING.value:
            self.publish_download(row)
        self.wake()
        return row

    async def remove(self, job_id: int) -> bool:
        async with self._lock:
            return await self._remove(job_id)

    async def _remove(self, job_id: int) -> bool:
        if await self._stop_active(job_id, "remove"):
            self.bus.publish("queue.changed", {}, audience="all")
            return True

        def run(session: Session) -> tuple[int, str] | None:
            row = session.get(Download, job_id)
            if row is None:
                return None
            owner = (row.user_id, row.provider)
            session.delete(row)
            return owner

        owner = await self._db(run)
        if owner is None:
            return False
        user_id = owner[0]
        self._release_provider_session(job_id)
        await asyncio.to_thread(self.storage.remove_job_temp, job_id)
        self.bus.publish("download.removed", {"id": job_id}, user_id=user_id)
        self.bus.publish("queue.changed", {}, audience="all")
        return True

    async def get(self, job_id: int) -> Download | None:
        def run(session: Session) -> Download | None:
            row = session.get(Download, job_id)
            if row is not None:
                return row

        return await self._db(run)

    # ------------------------------------------- provider-facing runtime hooks

    def job_status(self, job_id: int) -> dict[str, Any] | None:
        handle = self._active.get(job_id)
        if handle is None:
            return None
        return {
            "id": job_id,
            "downloaded_bytes": handle.downloaded,
            "total_bytes": handle.total,
            "speed_bps": handle.speed,
            "transferring": handle.transferring,
        }

    async def cancel_job(self, job_id: int) -> bool:
        return (await self.cancel(job_id)) is not None

    def live(self, job_id: int) -> JobHandle | None:
        return self._active.get(job_id)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "active_jobs": len(self._active),
            "transferring": sum(1 for h in self._active.values() if h.transferring),
            "aggregate_speed_bps": self.aggregate_speed(),
            "started_at": self.started_at,
        }


def _detach(row: Download) -> Download:
    return row


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["DownloadManager", "FINISHED_STATES"]
