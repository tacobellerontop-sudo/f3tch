"""Update checks against GitHub releases, and starting the updater.

The check is one anonymous ``GET`` to GitHub's public API for the configured
repository; nothing about this installation is sent. Installing an update is
never automatic: an administrator starts it, and the actual work is done by
``update.ps1`` (the same script used from the command line), which verifies the
release package's checksum before using it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import BootConfig
from .engine.http import HttpClient
from .providers.base import ProviderError

log = logging.getLogger("fetch.updates")

CHECK_INTERVAL = 6 * 3600
#: A run that reported "running" longer ago than this is considered dead.
RUN_TIMEOUT = 45 * 60
_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?")
MAX_NOTES = 20_000


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.match((text or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class UpdateError(Exception):
    """The updater could not be started; the message is safe to show."""


class UpdateService:
    def __init__(self, boot: BootConfig, http: HttpClient) -> None:
        self.boot = boot
        self.http = http
        self._release: dict[str, Any] | None = None
        self._error = ""
        self._checked_at: str | None = None
        self._checked_monotonic = 0.0

    # -- checking ----------------------------------------------------------------

    @property
    def repository(self) -> str:
        repo = self.boot.update_repository
        return repo if _REPOSITORY.match(repo) else ""

    def _stale(self) -> bool:
        return self._checked_at is None or time.monotonic() - self._checked_monotonic > CHECK_INTERVAL

    async def check(self) -> None:
        """Ask GitHub for the latest published release and remember the answer."""
        self._checked_at, self._checked_monotonic = _now(), time.monotonic()
        self._release, self._error = None, ""
        if not self.repository:
            self._error = "Update checks are switched off (no repository is configured)."
            return
        url = f"{self.boot.update_api_url}/repos/{self.repository}/releases/latest"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "FETCH-update-check",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            status, payload = await self.http.get_json("GET", url, headers=headers)
        except ProviderError as exc:
            self._error = f"GitHub could not be reached: {exc.message}"
            return
        except Exception as exc:  # network stack errors of any kind
            log.warning("Update check failed", extra={"reason": type(exc).__name__})
            self._error = "GitHub could not be reached. Check the server's internet connection."
            return
        if status == 404:
            self._error = (
                "No published release was found. The repository may not have a release "
                "yet, or it is private."
            )
        elif status in (403, 429):
            self._error = "GitHub is rate limiting this address. Try again in an hour."
        elif status != 200 or not isinstance(payload, dict):
            self._error = f"GitHub answered unexpectedly ({status})."
        elif parse_version(str(payload.get("tag_name") or "")) is None:
            self._error = "The latest release has no recognisable version number."
        else:
            self._release = payload

    def _release_info(self) -> dict[str, Any]:
        release = self._release
        if not release:
            return {
                "latest_version": None, "update_available": False, "release_name": "",
                "notes": "", "published_at": None, "release_url": "", "package_available": False,
            }
        tag = str(release.get("tag_name") or "")
        version = ".".join(str(part) for part in parse_version(tag) or ())
        url = str(release.get("html_url") or "")
        assets = release.get("assets") if isinstance(release.get("assets"), list) else []
        names = {str(a.get("name") or "") for a in assets if isinstance(a, dict)}
        package = any(re.fullmatch(r"FETCH-[\w.-]+\.zip", name) for name in names)
        return {
            "latest_version": version,
            "update_available": is_newer(tag, __version__),
            "release_name": str(release.get("name") or tag)[:200],
            "notes": str(release.get("body") or "")[:MAX_NOTES],
            "published_at": release.get("published_at") if isinstance(release.get("published_at"), str) else None,
            # Only ever link to GitHub itself.
            "release_url": url if url.startswith("https://github.com/") else "",
            "package_available": package,
        }

    # -- the updater --------------------------------------------------------------

    def last_run(self) -> dict[str, Any] | None:
        """What ``update.ps1`` recorded about its most recent run."""
        try:
            raw = json.loads(self.boot.update_status_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        return {
            "state": str(raw.get("state") or "")[:20],
            "message": str(raw.get("message") or "")[:500],
            "from_version": str(raw.get("from_version") or "")[:40],
            "to_version": str(raw.get("to_version") or "")[:40],
            "started_at": str(raw.get("started_at") or "")[:40],
            "finished_at": str(raw.get("finished_at") or "")[:40],
        }

    def in_progress(self) -> bool:
        run = self.last_run()
        if not run or run["state"] != "running":
            return False
        try:
            started = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
        except ValueError:
            return False
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return (datetime.now(UTC) - started).total_seconds() < RUN_TIMEOUT

    def _powershell(self) -> str | None:
        return shutil.which("powershell") or shutil.which("pwsh")

    @property
    def script(self) -> Path:
        return self.boot.home / "update.ps1"

    def apply_blocker(self, *, managed: bool) -> str:
        """Why the update cannot be installed from the dashboard ('' if it can)."""
        if os.name != "nt":
            return (
                "Installing from the dashboard is available on Windows. On this system "
                "update the files and restart FETCH yourself (see docs/DEVELOPMENT.md)."
            )
        if not managed:
            return "This server was not started by start.ps1, so it cannot restart itself."
        if not self.script.is_file():
            return "update.ps1 is missing from the FETCH folder."
        if not self._powershell():
            return "PowerShell was not found on this computer."
        return ""

    def start(self, *, managed: bool) -> None:
        """Launch ``update.ps1`` detached. It stops, updates and restarts FETCH."""
        blocker = self.apply_blocker(managed=managed)
        if blocker:
            raise UpdateError(blocker)
        if self.in_progress():
            raise UpdateError("An update is already running.")
        command = [
            self._powershell() or "powershell", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(self.script), "-Yes", "-Unattended",
        ]
        self.boot.log_dir.mkdir(parents=True, exist_ok=True)
        # The updater must outlive this process, which it is about to stop.
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        with open(self.boot.log_dir / "update.log", "ab") as output:
            output.write(f"\n=== Update started from the dashboard at {_now()} ===\n".encode())
            output.flush()
            for flags in (no_window | new_group | breakaway, no_window | new_group):
                try:
                    subprocess.Popen(  # noqa: S603 - fixed command, no user input
                        command, cwd=str(self.boot.home), stdin=subprocess.DEVNULL,
                        stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                        creationflags=flags,
                    )
                    break
                except OSError as exc:
                    last = exc
            else:
                raise UpdateError(f"The updater could not be started: {last}")
        # Mark the run immediately, so a second click cannot start another one.
        try:
            self.boot.update_status_file.write_text(
                json.dumps({
                    "state": "running", "message": "Starting the updater.",
                    "from_version": __version__, "to_version": "",
                    "started_at": _now(), "finished_at": "",
                }),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- API payload ---------------------------------------------------------------

    async def status(self, *, auto_check: bool, refresh: bool, managed: bool) -> dict[str, Any]:
        if refresh or (auto_check and self._stale()):
            await self.check()
        blocker = self.apply_blocker(managed=managed)
        return {
            "current_version": __version__,
            "repository": self.repository,
            "auto_check": auto_check,
            "checked_at": self._checked_at,
            "error": self._error,
            **self._release_info(),
            "can_apply": not blocker,
            "apply_blocker": blocker,
            "source": "git" if (self.boot.home / ".git").exists() else "release",
            "in_progress": self.in_progress(),
            "last_run": self.last_run(),
        }
