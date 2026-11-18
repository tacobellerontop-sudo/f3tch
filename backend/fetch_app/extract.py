"""Extract downloaded archives automatically.

Supported: .zip and .tar(.gz/.bz2/.xz) with Python's own modules; .7z and
.rar (including multi-part sets such as ``name.part1.rar``, ``name.r00`` or
``name.7z.001``) with 7-Zip when it is installed. Archives are unpacked into a
new folder next to them, named after the archive. Paths that would escape that
folder are refused, and password-protected archives are reported, not opened.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("fetch.extract")

_TAR = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
_PART_RAR = re.compile(r"^(?P<base>.+)\.part(?P<n>\d+)\.rar$", re.I)
_OLD_RAR = re.compile(r"^(?P<base>.+)\.(?:rar|r\d\d)$", re.I)
_SPLIT = re.compile(r"^(?P<base>.+\.(?:7z|zip))\.(?P<n>\d{3})$", re.I)


class ExtractError(Exception):
    pass


@dataclass(frozen=True)
class ArchiveSet:
    #: The file to open (the first part for multi-part sets).
    head: Path
    #: Matches the file names of every part of the set.
    pattern: re.Pattern
    kind: str  # "zip" | "tar" | "7z"
    #: Name of the folder the files go into.
    folder_name: str


def archive_set(path: Path) -> ArchiveSet | None:
    """The archive ``path`` belongs to, or None when it is not an archive."""
    name = path.name
    low = name.lower()
    match = _PART_RAR.match(name)
    if match:
        base = match.group("base")
        width = len(match.group("n"))
        head = path.with_name(f"{base}.part{1:0{width}d}.rar")
        return ArchiveSet(head, re.compile(rf"^{re.escape(base)}\.part\d+\.rar$", re.I), "7z", base)
    match = _SPLIT.match(name)
    if match:
        base = match.group("base")
        stem = base.rsplit(".", 1)[0]
        return ArchiveSet(
            path.with_name(f"{base}.001"), re.compile(rf"^{re.escape(base)}\.\d{{3}}$", re.I), "7z", stem
        )
    if low.endswith(".zip"):
        return ArchiveSet(path, re.compile(rf"^{re.escape(name)}$"), "zip", name[:-4])
    for suffix in _TAR:
        if low.endswith(suffix):
            return ArchiveSet(path, re.compile(rf"^{re.escape(name)}$"), "tar", name[: -len(suffix)])
    if low.endswith(".7z"):
        return ArchiveSet(path, re.compile(rf"^{re.escape(name)}$"), "7z", name[:-3])
    match = _OLD_RAR.match(name)
    if match:
        base = match.group("base")
        return ArchiveSet(
            path.with_name(f"{base}.rar"), re.compile(rf"^{re.escape(base)}\.(?:rar|r\d\d)$", re.I), "7z", base
        )
    return None


def parts_present(archive: ArchiveSet) -> list[Path]:
    folder = archive.head.parent
    try:
        return sorted(p for p in folder.iterdir() if p.is_file() and archive.pattern.match(p.name))
    except OSError:
        return []


def find_7zip() -> str | None:
    for name in ("7z", "7za", "7zz"):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    if sys.platform == "win32":
        for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            if os.environ.get(env):
                candidates.append(Path(os.environ[env]) / "7-Zip" / "7z.exe")
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    candidates += [base / "7z.exe", base / "tools" / "7z.exe", base / "tools" / "7-Zip" / "7z.exe"]
    return next((str(c) for c in candidates if c.is_file()), None)


def _target(archive: ArchiveSet) -> Path:
    folder = archive.head.parent / (archive.folder_name.strip(" .") or "extracted")
    candidate, counter = folder, 2
    while candidate.exists():
        candidate = folder.with_name(f"{folder.name} ({counter})")
        counter += 1
    return candidate


def _safe_member(root: Path, name: str) -> Path:
    destination = (root / name).resolve()
    if destination != root and root not in destination.parents:
        raise ExtractError(f"The archive contains a path outside its folder ({name}).")
    return destination


def _extract_zip(head: Path, target: Path) -> None:
    try:
        with zipfile.ZipFile(head) as archive:
            members = archive.infolist()
            if any(info.flag_bits & 0x1 for info in members):
                raise ExtractError("The archive is password-protected; FETCH does not open it.")
            root = target.resolve()
            for info in members:
                _safe_member(root, info.filename)
            archive.extractall(target)
    except zipfile.BadZipFile as exc:
        raise ExtractError("The file is not a valid zip archive.") from exc


def _extract_tar(head: Path, target: Path) -> None:
    try:
        with tarfile.open(head) as archive:
            archive.extractall(target, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise ExtractError(f"The tar archive could not be extracted ({exc}).") from exc


def _extract_7z(head: Path, target: Path) -> None:
    tool = find_7zip()
    if tool is None:
        raise ExtractError(
            "Extracting .7z and .rar needs 7-Zip. Install it (winget install 7zip.7zip) and FETCH uses it."
        )
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
    # -p- refuses passwords instead of asking; -snl- never creates symbolic links.
    result = subprocess.run(
        [tool, "x", "-y", "-p-", "-snl-", f"-o{target}", str(head)],
        capture_output=True, text=True, errors="replace", creationflags=flags, timeout=6 * 3600,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr).lower()
        if "wrong password" in output or "encrypted" in output or "can not open encrypted" in output:
            raise ExtractError("The archive is password-protected; FETCH does not open it.")
        if "missing volume" in output or "unexpected end" in output:
            raise ExtractError("A part of the archive is missing or incomplete.")
        raise ExtractError(f"7-Zip could not extract the archive (code {result.returncode}).")
    root = target.resolve()
    for current, _dirs, files in os.walk(target):
        for name in files:
            _safe_member(root, str(Path(current, name).relative_to(target)))


def extract(archive: ArchiveSet, *, delete_after: bool = False) -> Path:
    """Unpack ``archive`` into a new folder next to it and return that folder."""
    if not archive.head.is_file():
        raise ExtractError("The first part of the archive is missing.")
    target = _target(archive)
    target.mkdir(parents=True)
    try:
        {"zip": _extract_zip, "tar": _extract_tar, "7z": _extract_7z}[archive.kind](archive.head, target)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    if delete_after:
        for part in parts_present(archive):
            try:
                part.unlink()
            except OSError:
                log.warning("Could not delete %s after extracting it", part.name)
    return target
