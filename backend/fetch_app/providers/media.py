"""YouTube and other video/audio sites, through yt-dlp (https://github.com/yt-dlp/yt-dlp).

yt-dlp knows how each site publishes its streams; FETCH hands it the link and
keeps the queue, progress, speed limit and storage. Its network traffic goes
through FETCH's guard proxy, so it cannot reach private addresses either.

Spotify links are recognised only to explain why they cannot be downloaded:
Spotify's audio is protected by DRM, and FETCH does not decrypt DRM.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

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

if TYPE_CHECKING:  # pragma: no cover
    from ..engine.transfer import Transfer

log = logging.getLogger("fetch.provider.media")

#: Sites handed to yt-dlp. Matching is by host name (and its subdomains).
MEDIA_HOSTS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "vimeo.com", "dailymotion.com", "dai.ly",
    "twitch.tv", "soundcloud.com", "bandcamp.com", "mixcloud.com",
    "tiktok.com", "x.com", "twitter.com", "reddit.com", "v.redd.it",
    "instagram.com", "facebook.com", "fb.watch",
    "bilibili.com", "bilibili.tv", "nicovideo.jp", "rumble.com", "odysee.com",
    "streamable.com", "archive.org", "ted.com", "kick.com", "bsky.app",
)
DRM_HOSTS = ("spotify.com", "spotify.link", "music.apple.com", "tidal.com", "deezer.com")

QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "audio")


def _host_matches(url: str, hosts: tuple[str, ...]) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def find_ffmpeg() -> str | None:
    """ffmpeg on PATH, or next to FETCH.exe / in its ``tools`` folder."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    for folder in (base, base / "tools", base / "ffmpeg" / "bin"):
        for name in ("ffmpeg.exe", "ffmpeg"):
            if (folder / name).is_file():
                return str(folder / name)
    return None


def find_js_runtime() -> dict[str, dict[str, str]] | None:
    """The JavaScript runtime yt-dlp needs for YouTube's player challenges."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:  # FETCH.exe carries Deno next to its other files
        for name in ("deno.exe", "deno"):
            candidate = Path(bundled) / "deno_bin" / name
            if candidate.is_file():
                return {"deno": {"path": str(candidate)}}
    try:
        import deno  # the "deno" wheel ships the Deno executable

        path = deno.find_deno_bin()
        if path and os.path.exists(path):
            return {"deno": {"path": str(path)}}
    except Exception:  # pragma: no cover - depends on the installation
        pass
    if shutil.which("deno"):
        return {"deno": {}}
    return None


def format_for(quality: str, ffmpeg: bool) -> str:
    """yt-dlp format selector for a quality setting."""
    if quality == "audio":
        return "ba[ext=m4a]/ba/b"
    height = f"[height<={quality}]" if quality.isdigit() else ""
    if ffmpeg:
        return f"bv*{height}+ba/b{height}/b" if height else "bv*+ba/b"
    # Without ffmpeg only files that already contain picture and sound work.
    return f"b{height}/b" if height else "b"


class _QuietLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)


def map_error(message: str) -> ProviderError:
    """Turn a yt-dlp error message into a :class:`ProviderError`."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", message or "").replace("ERROR: ", "").strip()
    low = text.lower()
    short = text.splitlines()[0][:300] if text else "yt-dlp could not download this link."
    if "unsupported url" in low:
        return ProviderError(ErrorCode.UNSUPPORTED, "This page has no video or audio FETCH can download.")
    if "drm" in low:
        return ProviderError(ErrorCode.UNSUPPORTED, "This media is protected by DRM, which FETCH does not remove.")
    if any(k in low for k in ("private video", "video unavailable", "has been removed", "not available",
                              "does not exist", "404")):
        return ProviderError(ErrorCode.NOT_FOUND, short)
    if any(k in low for k in ("sign in", "log in", "login required", "age", "members-only", "cookies")):
        return ProviderError(
            ErrorCode.AUTH_REQUIRED,
            f"{short} You can paste your browser's cookies for the site on the Providers page.",
        )
    if "429" in low or "too many requests" in low:
        return ProviderError(ErrorCode.RATE_LIMITED, short, retryable=True)
    if any(k in low for k in ("timed out", "connection", "network", "temporarily", "503", "502", "500")):
        return ProviderError(ErrorCode.NETWORK, short, retryable=True)
    return ProviderError(ErrorCode.PROTOCOL, short)


class MediaProvider(ProviderAdapter):
    id = "media"
    name = "YouTube & media sites"
    description = "Videos and audio from YouTube, Vimeo, SoundCloud, TikTok, Twitch and more (yt-dlp)."
    homepage = "https://github.com/yt-dlp/yt-dlp"
    priority = 40
    capabilities = Capabilities(credentials=True, resume=True)
    auth_modes = (AuthMode.ANONYMOUS, AuthMode.ACCOUNT)
    credential_fields = (
        CredentialField(
            "cookies",
            "Cookies (cookies.txt)",
            kind="textarea",
            help="Optional. Only needed for age-restricted, members-only or sign-in videos. "
            "Export cookies.txt for the site from your browser and paste it here.",
        ),
    )
    notes = (
        "Quality is chosen under Settings > Downloads. Without ffmpeg, YouTube offers "
        "picture and sound together only up to 720p; install ffmpeg (winget install ffmpeg) "
        "for 1080p, 4K and best quality.",
        "Playlist and channel links download the single video in the link, if there is one.",
        "Spotify, Apple Music, Tidal and Deezer are DRM-protected and are not supported.",
    )
    verification = (
        "Uses yt-dlp, which is maintained against these sites. FETCH's integration is "
        "tested against a local stand-in; the live sites were not reachable from the "
        "test environment."
    )

    def __init__(self) -> None:
        self.extra_options: dict[str, Any] = {}

    # -- recognition -----------------------------------------------------------

    def can_handle(self, url: str) -> bool:
        return _host_matches(url, MEDIA_HOSTS) or _host_matches(url, DRM_HOSTS)

    @staticmethod
    def _refuse_drm(url: str) -> None:
        if _host_matches(url, DRM_HOSTS):
            raise ProviderError(
                ErrorCode.UNSUPPORTED,
                "Music from Spotify (and Apple Music, Tidal, Deezer) is protected by DRM. "
                "FETCH does not remove DRM, so these links cannot be downloaded.",
            )

    # -- settings --------------------------------------------------------------

    def _settings(self) -> tuple[str, int, int]:
        """(quality, connections, speed limit in bytes/s) from the app settings."""
        runtime = getattr(self, "runtime", None)
        if runtime is None:
            return "best", 4, 0
        downloads = runtime.settings.get().downloads
        return (
            downloads.media_quality,
            downloads.connections_per_download,
            downloads.speed_limit_kbps * 1024,
        )

    def _options(self, ctx: ProviderContext, quality: str, workdir: Path | None) -> dict[str, Any]:
        ffmpeg = find_ffmpeg()
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "logger": _QuietLogger(),
            "socket_timeout": 30,
            "retries": 10,
            "fragment_retries": 10,
            "cachedir": False,
            "format": format_for(quality, bool(ffmpeg)),
            # Prefer files that play everywhere (H.264/AAC in MP4) at equal quality.
            "format_sort": ["res", "fps", "ext:mp4:m4a"],
            "merge_output_format": "mp4/mkv",
        }
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
        js = find_js_runtime()
        if js:
            opts["js_runtimes"] = js
        if ctx.proxy_url:
            opts["proxy"] = ctx.proxy_url
        if workdir is not None:
            opts["paths"] = {"home": str(workdir), "temp": str(workdir)}
            opts["outtmpl"] = {"default": "media.%(ext)s"}
        opts.update(self.extra_options)
        return opts

    @contextlib.contextmanager
    def _cookies(self, ctx: ProviderContext, opts: dict[str, Any], folder: Path):
        text = (ctx.credentials or {}).get("cookies", "")
        if not text:
            yield
            return
        path = folder / f"cookies-{threading.get_ident()}.txt"
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        opts["cookiefile"] = str(path)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                path.unlink()

    # -- metadata --------------------------------------------------------------

    async def _extract(self, url: str, ctx: ProviderContext) -> dict[str, Any]:
        self._refuse_drm(url)
        quality, _connections, _rate = self._settings()
        opts = self._options(ctx, quality, None)

        def work() -> dict[str, Any]:
            import tempfile

            import yt_dlp

            with tempfile.TemporaryDirectory(prefix="fetch-ytdlp-") as tmp, \
                    self._cookies(ctx, opts, Path(tmp)):
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return ydl.sanitize_info(info)

        try:
            info = await asyncio.to_thread(work)
        except ProviderError:
            raise
        except Exception as exc:
            raise map_error(str(exc)) from exc
        if not isinstance(info, dict):
            raise ProviderError(ErrorCode.PROTOCOL, "yt-dlp returned nothing for this link.")
        if info.get("_type") == "playlist":
            raise ProviderError(
                ErrorCode.UNSUPPORTED,
                "This is a playlist or channel. Add the links of the individual videos instead.",
            )
        return info

    @staticmethod
    def _approx_size(info: dict[str, Any]) -> int | None:
        parts = info.get("requested_formats") or [info]
        total = 0
        for part in parts:
            size = part.get("filesize") or part.get("filesize_approx")
            if not size:
                return None
            total += int(size)
        return total or None

    @staticmethod
    def _filename(info: dict[str, Any]) -> str:
        title = str(info.get("title") or info.get("id") or "media").strip()
        ext = str(info.get("ext") or "mp4")
        video_id = str(info.get("id") or "")
        suffix = f" [{video_id}]" if video_id and video_id not in title else ""
        return f"{title[:180]}{suffix}.{ext}"

    async def inspect(self, url: str, ctx: ProviderContext) -> LinkInfo:
        info = await self._extract(url, ctx)
        return LinkInfo(
            url=url,
            provider=self.id,
            filename=self._filename(info),
            size=self._approx_size(info),
            resumable=True,
        )

    async def resolve(self, url: str, ctx: ProviderContext) -> ResolvedDownload:
        info = await self._extract(url, ctx)
        resolved = ResolvedDownload(
            url=url,
            filename=self._filename(info),
            # yt-dlp's sizes are often estimates, so the file is not checked against one.
            size=None,
            resumable=True,
            identity=f"media:{info.get('extractor_key')}:{info.get('id')}:{info.get('format_id')}",
            allow_aria2=False,
        )
        resolved.media_context = ctx  # type: ignore[attr-defined]
        resolved.media_estimate = self._approx_size(info)  # type: ignore[attr-defined]
        return resolved

    # -- downloading -----------------------------------------------------------

    async def download(self, transfer: Transfer, resolved: ResolvedDownload) -> None:
        transfer.engine = "yt-dlp"
        ctx: ProviderContext = getattr(resolved, "media_context")
        estimate: int | None = getattr(resolved, "media_estimate", None)
        quality, connections, rate = self._settings()
        workdir = transfer.destination.parent / "media"
        workdir.mkdir(parents=True, exist_ok=True)
        opts = self._options(ctx, quality, workdir)
        opts["concurrent_fragment_downloads"] = max(1, min(connections, 16))
        # Requests in 10 MiB pieces keep YouTube from throttling a long stream.
        opts["http_chunk_size"] = 10 * 1024 * 1024
        opts["continuedl"] = True
        if rate > 0:
            opts["ratelimit"] = rate

        loop = asyncio.get_running_loop()
        abort = threading.Event()
        problem: list[ProviderError] = []
        finished: dict[str, int] = {}
        current: dict[str, int] = {}

        def report(done: int, total: int | None) -> None:
            try:
                transfer.report(done, total)
            except ProviderError as exc:
                problem.append(exc)
                abort.set()

        def hook(status: dict[str, Any]) -> None:
            if abort.is_set():
                import yt_dlp

                raise yt_dlp.utils.DownloadCancelled("Stopped by FETCH")
            name = str(status.get("filename") or "")
            got = int(status.get("downloaded_bytes") or 0)
            size = status.get("total_bytes") or status.get("total_bytes_estimate")
            if status.get("status") == "finished":
                finished[name] = int(size or got)
                current.pop(name, None)
            elif status.get("status") == "downloading":
                current[name] = got
            done = sum(finished.values()) + sum(current.values())
            total = max(estimate or 0, done) or None
            loop.call_soon_threadsafe(report, done, total)

        opts["progress_hooks"] = [hook]

        def work() -> None:
            import yt_dlp

            with self._cookies(ctx, opts, workdir), yt_dlp.YoutubeDL(opts) as ydl:
                code = ydl.download([resolved.url])
            if code:
                raise ProviderError(ErrorCode.PROTOCOL, "yt-dlp could not finish this download.",
                                    retryable=True)

        future = loop.run_in_executor(None, work)
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            abort.set()
            with contextlib.suppress(BaseException):
                await future  # let yt-dlp close its files before FETCH moves on
            raise
        except ProviderError:
            if problem:
                raise problem[0] from None
            raise
        except Exception as exc:
            if problem:
                raise problem[0] from None
            raise map_error(str(exc)) from exc

        produced = [
            p for p in workdir.iterdir()
            if p.is_file() and p.name.startswith("media.")
            and not p.name.endswith((".part", ".ytdl", ".temp")) and ".part-Frag" not in p.name
            and not re.match(r"media\.f[\w-]+\.", p.name)
        ]
        if not produced:
            raise ProviderError(ErrorCode.PROTOCOL, "yt-dlp finished without producing a file.",
                                retryable=True)
        final = max(produced, key=lambda p: p.stat().st_size)
        os.replace(final, transfer.destination)
        shutil.rmtree(workdir, ignore_errors=True)
        size = transfer.destination.stat().st_size
        transfer.report(size, size)

    # -- health ----------------------------------------------------------------

    async def authenticate(self, ctx: ProviderContext) -> HealthReport:
        if (ctx.credentials or {}).get("cookies"):
            return HealthReport(ok=True, message="Cookies are stored and sent to the sites you download from.")
        return await self.test(ctx)

    async def test(self, ctx: ProviderContext) -> HealthReport:
        try:
            import yt_dlp
        except ImportError:
            return HealthReport(ok=False, message="yt-dlp is not installed.")
        parts = [f"yt-dlp {yt_dlp.version.__version__} is ready."]
        parts.append("ffmpeg found: best quality is available." if find_ffmpeg()
                     else "ffmpeg not found: YouTube is limited to 720p (install ffmpeg for more).")
        if not find_js_runtime():
            parts.append("Deno was not found, so some YouTube formats may be missing.")
        return HealthReport(ok=True, message=" ".join(parts))
