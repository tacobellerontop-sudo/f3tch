"""Storage manager: where files live, and the only code that builds paths.

Layout under the storage root::

    downloads/<user-id>/...   private files of each user
    shared/...                optional shared space
    temporary/<download-id>/  in-progress downloads

Every path that originates outside FETCH (API parameters, provider-supplied
file names) goes through :meth:`StorageManager.resolve` or
:func:`sanitize_filename`, which reject traversal and normalise names so they
are valid on NTFS.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SCOPES = ("private", "shared")
MAX_NAME_LENGTH = 200
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{dev}{n}" for dev in ("COM", "LPT") for n in "0123456789"
}

FILE_TYPES: dict[str, set[str]] = {
    "video": {"mp4", "mkv", "avi", "mov", "wmv", "webm", "m4v", "flv", "mpg", "mpeg", "ts"},
    "audio": {"mp3", "flac", "wav", "aac", "ogg", "m4a", "opus", "wma"},
    "image": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "tiff", "heic", "avif"},
    "archive": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst", "iso", "cab"},
    "document": {
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md", "epub", "csv", "rtf",
        "odt", "json", "xml", "html",
    },
    "application": {"exe", "msi", "apk", "dmg", "deb", "rpm", "appimage", "bat", "ps1", "sh"},
}


class StorageError(Exception):
    """A storage operation was refused or failed."""


def file_type(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    for kind, extensions in FILE_TYPES.items():
        if ext in extensions:
            return kind
    return "other"


def sanitize_filename(name: str | None, fallback: str = "download") -> str:
    """Turn an untrusted name into a single safe path component."""
    name = unicodedata.normalize("NFC", name or "")
    # Keep only the last component of anything that looks like a path.
    name = re.split(r"[/\\]", name)[-1]
    name = _INVALID_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name or set(name) <= {"_", "."}:
        name = fallback
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.split(".")[0].upper() in _RESERVED:
        stem = f"_{stem}"
    ext = ext[:20]
    limit = MAX_NAME_LENGTH - (len(ext) + 1 if ext else 0)
    stem = stem[:limit].rstrip(" .") or fallback
    return f"{stem}.{ext}" if ext else stem


def clean_rel_path(rel_path: str | None) -> str:
    """Validate a user-supplied relative path. Returns it in POSIX form.

    Unlike :func:`sanitize_filename` this *rejects* suspicious input instead of
    repairing it, so a caller can never address a file it did not name exactly.
    """
    raw = (rel_path or "").replace("\\", "/").strip()
    if raw in ("", ".", "/"):
        return ""
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or "\x00" in raw:
        raise StorageError("Absolute paths are not allowed.")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    for part in parts:
        if part == ".." or sanitize_filename(part, fallback="") != part:
            raise StorageError("The path contains an invalid name.")
    if len(parts) > 32:
        raise StorageError("The path is nested too deeply.")
    return "/".join(parts)


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int
    modified: float
    type: str


class StorageManager:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._usage_cache: dict[tuple[str, int], tuple[float, int]] = {}
        self._lock = threading.Lock()
        self._finalize_lock = threading.Lock()

    # -- layout ----------------------------------------------------------------

    @property
    def downloads_dir(self) -> Path:
        return self.root / "downloads"

    @property
    def shared_dir(self) -> Path:
        return self.root / "shared"

    @property
    def temp_dir(self) -> Path:
        return self.root / "temporary"

    def ensure(self) -> None:
        for path in (self.root, self.downloads_dir, self.shared_dir, self.temp_dir):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise StorageError(f"Cannot create storage directory {path}: {exc}") from exc

    def space_root(self, scope: str, user_id: int) -> Path:
        if scope == "shared":
            root = self.shared_dir
        elif scope == "private":
            root = self.downloads_dir / str(int(user_id))
        else:
            raise StorageError("Unknown storage scope.")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def resolve(self, scope: str, user_id: int, rel_path: str | None) -> Path:
        """Map a (scope, owner, relative path) to an absolute path inside that space."""
        root = self.space_root(scope, user_id).resolve()
        rel = clean_rel_path(rel_path)
        target = (root / rel).resolve() if rel else root
        if target != root and not target.is_relative_to(root):
            raise StorageError("The path escapes the storage area.")
        return target

    def job_temp_dir(self, download_id: int) -> Path:
        path = self.temp_dir / str(int(download_id))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def remove_job_temp(self, download_id: int) -> None:
        shutil.rmtree(self.temp_dir / str(int(download_id)), ignore_errors=True)

    # -- finalization ----------------------------------------------------------

    def finalize(self, temp_file: Path, scope: str, user_id: int, folder: str, name: str) -> Path:
        """Atomically move a finished download into place under a unique name."""
        dest_dir = self.resolve(scope, user_id, folder)
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = sanitize_filename(name)
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        with self._finalize_lock:
            candidate = dest_dir / name
            counter = 1
            while candidate.exists():
                suffix = f" ({counter})"
                trimmed = stem[: MAX_NAME_LENGTH - len(suffix) - len(ext) - 1]
                candidate = dest_dir / (f"{trimmed}{suffix}.{ext}" if ext else f"{trimmed}{suffix}")
                counter += 1
            try:
                os.replace(temp_file, candidate)
            except OSError:
                # Different volume: copy next to the target, then rename atomically.
                partial = candidate.with_name(candidate.name + ".fetch-partial")
                try:
                    shutil.copyfile(temp_file, partial)
                    os.replace(partial, candidate)
                    os.unlink(temp_file)
                except OSError as exc:
                    partial.unlink(missing_ok=True)
                    raise StorageError(f"Could not move the finished file into place: {exc}") from exc
        self.invalidate_usage(scope, user_id)
        return candidate

    def rel_to_space(self, scope: str, user_id: int, path: Path) -> str:
        return path.resolve().relative_to(self.space_root(scope, user_id).resolve()).as_posix()

    # -- usage -----------------------------------------------------------------

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        stack = [path]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                total += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
        return total

    def usage(self, scope: str, user_id: int = 0, *, max_age: float = 300.0) -> int:
        key = (scope, user_id if scope == "private" else 0)
        with self._lock:
            cached = self._usage_cache.get(key)
            if cached and time.monotonic() - cached[0] < max_age:
                return cached[1]
        size = self._dir_size(self.space_root(scope, user_id))
        with self._lock:
            self._usage_cache[key] = (time.monotonic(), size)
        return size

    def invalidate_usage(self, scope: str | None = None, user_id: int = 0) -> None:
        with self._lock:
            if scope is None:
                self._usage_cache.clear()
            else:
                self._usage_cache.pop((scope, user_id if scope == "private" else 0), None)

    def total_usage(self) -> int:
        return self._dir_size(self.downloads_dir) + self._dir_size(self.shared_dir)

    def disk(self) -> dict[str, int]:
        try:
            usage = shutil.disk_usage(self.root)
            return {"total": usage.total, "used": usage.used, "free": usage.free}
        except OSError:
            return {"total": 0, "used": 0, "free": 0}

    # -- browsing --------------------------------------------------------------

    def list_dir(self, scope: str, user_id: int, rel_path: str) -> list[Entry]:
        directory = self.resolve(scope, user_id, rel_path)
        if not directory.is_dir():
            raise StorageError("Folder not found.")
        return self._scan(directory, clean_rel_path(rel_path))

    @staticmethod
    def _scan(directory: Path, rel: str) -> list[Entry]:
        entries: list[Entry] = []
        with os.scandir(directory) as it:
            for item in it:
                try:
                    if item.is_symlink() or item.name.endswith(".fetch-partial"):
                        continue
                    stat = item.stat(follow_symlinks=False)
                    is_dir = item.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                entries.append(
                    Entry(
                        name=item.name,
                        path=str(PurePosixPath(rel) / item.name) if rel else item.name,
                        is_dir=is_dir,
                        size=0 if is_dir else stat.st_size,
                        modified=stat.st_mtime,
                        type="folder" if is_dir else file_type(item.name),
                    )
                )
        return entries

    def search(self, scope: str, user_id: int, rel_path: str, query: str, limit: int = 500):
        """Recursive, case-insensitive name search below ``rel_path``."""
        base = self.resolve(scope, user_id, rel_path)
        if not base.is_dir():
            raise StorageError("Folder not found.")
        needle = query.lower()
        found: list[Entry] = []
        stack = [(base, clean_rel_path(rel_path))]
        while stack and len(found) < limit:
            directory, rel = stack.pop()
            try:
                children = self._scan(directory, rel)
            except OSError:
                continue
            for entry in children:
                if entry.is_dir:
                    stack.append((directory / entry.name, entry.path))
                if needle in entry.name.lower():
                    found.append(entry)
        return found[:limit]

    def make_folder(self, scope: str, user_id: int, rel_path: str, name: str) -> Path:
        clean = sanitize_filename(name, fallback="")
        if not clean or clean != name.strip():
            raise StorageError("That folder name is not allowed.")
        parent = self.resolve(scope, user_id, rel_path)
        if not parent.is_dir():
            raise StorageError("Parent folder not found.")
        target = parent / clean
        if target.exists():
            raise StorageError("A file or folder with that name already exists.")
        target.mkdir()
        return target

    def delete(self, scope: str, user_id: int, rel_path: str) -> int:
        """Delete a file or folder. Returns the number of bytes freed."""
        if not clean_rel_path(rel_path):
            raise StorageError("The root folder cannot be deleted.")
        target = self.resolve(scope, user_id, rel_path)
        if not target.exists():
            raise StorageError("File not found.")
        if target.is_dir():
            freed = self._dir_size(target)
            shutil.rmtree(target)
        else:
            freed = target.stat().st_size
            target.unlink()
        self.invalidate_usage(scope, user_id)
        return freed

    # -- cleanup ---------------------------------------------------------------

    def cleanup_temp(self, keep_ids: set[int], max_age_hours: float) -> int:
        """Remove temporary folders that belong to no open download and are old."""
        removed = 0
        cutoff = time.time() - max_age_hours * 3600
        if not self.temp_dir.is_dir():
            return 0
        for item in self.temp_dir.iterdir():
            try:
                job_id = int(item.name)
            except ValueError:
                job_id = -1
            if job_id in keep_ids:
                continue
            try:
                newest = max(
                    (p.stat().st_mtime for p in item.rglob("*")), default=item.stat().st_mtime
                )
            except OSError:
                continue
            if newest < cutoff:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
                removed += 1
        return removed
