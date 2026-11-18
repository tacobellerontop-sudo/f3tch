"""Link grabbing: notice download links copied anywhere on this computer.

While **Settings > General > Watch the clipboard for links** is on, FETCH
reads the clipboard text about once a second. When newly copied text holds
links that FETCH can download, the dashboard is told about them and offers to
add them. The clipboard is only read on this computer; nothing is stored or
sent anywhere, and FETCH never adds a download on its own.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sys
from collections.abc import Callable
from urllib.parse import urlsplit

log = logging.getLogger("fetch.clipboard")

POLL_SECONDS = 1.0
MAX_LINKS = 100
MAX_TEXT = 512 * 1024

_URL = re.compile(r"""https?://[^\s"'<>`{}|\\^\[\]]+""", re.I)
_TRAILING = ".,;:!?)]}>'\""

#: Extensions that make a plain web link worth offering (others are pages).
FILE_EXTENSIONS = {
    "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "iso", "img", "dmg",
    "exe", "msi", "msix", "appx", "apk", "deb", "rpm", "appimage", "jar",
    "mp4", "mkv", "avi", "mov", "webm", "wmv", "flv", "m4v", "ts",
    "mp3", "flac", "wav", "m4a", "aac", "ogg", "opus",
    "pdf", "epub", "cbz", "cbr", "docx", "xlsx", "pptx",
    "bin", "torrent", "pak", "vpk",
}


def extract_urls(text: str) -> list[str]:
    """Every distinct http(s) link in ``text``, in order."""
    seen: set[str] = set()
    found: list[str] = []
    for match in _URL.finditer(text[:MAX_TEXT]):
        url = match.group(0)
        while url and url[-1] in _TRAILING:
            # Keep a closing bracket that belongs to the link itself.
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            url = url[:-1]
        if url and url not in seen:
            seen.add(url)
            found.append(url)
    return found


def looks_like_file(url: str) -> bool:
    path = urlsplit(url).path.lower()
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return False
    extension = name.rsplit(".", 1)[-1]
    return extension in FILE_EXTENSIONS or bool(re.fullmatch(r"r\d\d|\d{3}|part\d+", extension))


def windows_clipboard_text() -> str | None:  # pragma: no cover - Windows only
    """The clipboard's Unicode text, or None (no text, or the clipboard is busy)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    cf_unicodetext = 13
    if not user32.IsClipboardFormatAvailable(cf_unicodetext):
        return None
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(cf_unicodetext)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def default_reader() -> Callable[[], str | None] | None:
    return windows_clipboard_text if sys.platform == "win32" else None


class ClipboardWatcher:
    def __init__(
        self,
        *,
        bus,
        registry,
        settings,
        reader: Callable[[], str | None] | None = None,
        interval: float = POLL_SECONDS,
    ) -> None:
        self.bus = bus
        self.registry = registry
        self.settings = settings
        self.reader = reader
        self.interval = interval
        self._last: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        return self.reader is not None

    def start(self) -> None:
        if self.reader is not None and self._task is None:
            self._task = asyncio.create_task(self._run(), name="fetch-clipboard")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def downloadable(self, urls: list[str]) -> list[str]:
        """The links worth offering: a file host's link, or a link to a file."""
        keep = []
        for url in urls:
            provider = self.registry.detect(url)
            if provider is None:
                continue
            if provider.id != "direct" or looks_like_file(url):
                keep.append(url)
        return keep[:MAX_LINKS]

    def check(self, text: str | None) -> list[str]:
        """Process one clipboard reading; returns (and announces) new links."""
        if not text:
            return []
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        if digest == self._last:
            return []
        first = self._last is None
        self._last = digest
        if first:
            return []  # what was copied before FETCH started is not news
        urls = self.downloadable(extract_urls(text))
        if urls:
            self.bus.publish("clipboard.links", {"urls": urls}, audience="all")
        return urls

    async def _run(self) -> None:
        assert self.reader is not None
        while True:
            try:
                if self.settings.get().general.watch_clipboard:
                    text = await asyncio.to_thread(self.reader)
                    self.check(text)
                else:
                    self._last = None
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("Reading the clipboard failed", exc_info=True)
            await asyncio.sleep(self.interval)
