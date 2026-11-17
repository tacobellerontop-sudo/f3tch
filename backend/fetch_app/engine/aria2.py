"""Supervisor and JSON-RPC client for a private aria2 process.

aria2 is one of FETCH's transfer back-ends (multi-connection HTTP downloads);
job management stays in :mod:`fetch_app.engine.manager`. The RPC interface is
bound to 127.0.0.1 on a random port with a random secret and is never exposed
through the FETCH API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import aiohttp

from .guardproxy import GuardProxy
from .http import USER_AGENT

log = logging.getLogger("fetch.aria2")


class Aria2Error(Exception):
    pass


def find_aria2(configured: str = "", home: Path | None = None) -> str | None:
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    if home is not None:
        exe = "aria2c.exe" if os.name == "nt" else "aria2c"
        candidates.append(str(home / "tools" / "aria2" / exe))
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("aria2c") or shutil.which(
        "aria2c", path=str(Path(sys.executable).parent)
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Aria2Service:
    def __init__(self, executable: str | None, data_dir: Path, proxy: GuardProxy) -> None:
        self.executable = executable
        self._data_dir = data_dir
        self._proxy = proxy
        self._process: asyncio.subprocess.Process | None = None
        self._session: aiohttp.ClientSession | None = None
        self._secret = secrets.token_urlsafe(24)
        self._port = 0
        self.version = ""
        self.error = "" if executable else "aria2c was not found."

    @property
    def available(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> bool:
        if not self.executable:
            return False
        self._port = _free_port()
        conf = self._data_dir / "aria2.conf"
        conf.write_text(
            "\n".join(
                [
                    "enable-rpc=true",
                    "rpc-listen-all=false",
                    f"rpc-listen-port={self._port}",
                    f"rpc-secret={self._secret}",
                    "rpc-allow-origin-all=false",
                    f"all-proxy=http://127.0.0.1:{self._proxy.port}",
                    f"all-proxy-user={self._proxy.username}",
                    f"all-proxy-passwd={self._proxy.password}",
                    "proxy-method=tunnel",
                    "continue=true",
                    "auto-file-renaming=false",
                    "allow-overwrite=true",
                    "file-allocation=none",
                    "max-concurrent-downloads=64",
                    "min-split-size=1M",
                    "max-tries=3",
                    "retry-wait=2",
                    "connect-timeout=30",
                    "timeout=90",
                    "follow-torrent=false",
                    "follow-metalink=false",
                    "enable-dht=false",
                    "bt-enable-lpd=false",
                    "check-certificate=true",
                    "summary-interval=0",
                    "console-log-level=warn",
                    f"user-agent={USER_AGENT}",
                    f"stop-with-process={os.getpid()}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        with contextlib.suppress(OSError):
            os.chmod(conf, 0o600)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.executable,
                f"--conf-path={conf}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
                creationflags=flags,
            )
        except (OSError, NotImplementedError) as exc:
            self.error = f"aria2c could not be started: {exc}"
            log.warning(self.error)
            self._process = None
            return False
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        for _ in range(50):
            if self._process.returncode is not None:
                break
            try:
                info = await self.call("aria2.getVersion")
                self.version = info.get("version", "")
                self.error = ""
                log.info("aria2 engine started", extra={"version": self.version})
                return True
            except Aria2Error:
                await asyncio.sleep(0.1)
        self.error = "aria2c started but its RPC interface did not respond."
        log.warning(self.error)
        await self.stop()
        return False

    async def stop(self) -> None:
        if self._process is not None and self._process.returncode is None:
            with contextlib.suppress(Exception):
                await self.call("aria2.forceShutdown")
            # Every transfer was already removed (and flushed) by the manager, so
            # aria2 holds no state worth its multi-second graceful exit delay.
            for stop in (None, self._process.terminate, self._process.kill):
                if stop is not None:
                    with contextlib.suppress(ProcessLookupError, OSError):
                        stop()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=0.5 if stop is None else 3)
                    break
                except asyncio.TimeoutError:
                    continue
        self._process = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def call(self, method: str, *params: Any) -> Any:
        if self._session is None:
            raise Aria2Error("aria2 is not running.")
        payload = {
            "jsonrpc": "2.0",
            "id": secrets.token_hex(4),
            "method": method,
            "params": [f"token:{self._secret}", *params],
        }
        try:
            async with self._session.post(
                f"http://127.0.0.1:{self._port}/jsonrpc", json=payload
            ) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise Aria2Error(f"aria2 RPC failed: {type(exc).__name__}") from exc
        if "error" in data:
            raise Aria2Error(str(data["error"].get("message", "aria2 error")))
        return data.get("result")

    async def add(
        self,
        url: str,
        *,
        directory: Path,
        filename: str,
        headers: dict[str, str],
        connections: int,
        speed_limit: int,
    ) -> str:
        options: dict[str, Any] = {
            "dir": str(directory),
            "out": filename,
            "split": str(connections),
            "max-connection-per-server": str(connections),
            "header": [f"{k}: {v}" for k, v in headers.items()],
        }
        if speed_limit > 0:
            options["max-download-limit"] = str(speed_limit)
        return await self.call("aria2.addUri", [url], options)

    async def status(self, gid: str) -> dict[str, Any]:
        return await self.call(
            "aria2.tellStatus",
            gid,
            ["status", "completedLength", "totalLength", "errorCode", "errorMessage"],
        )

    async def remove(self, gid: str) -> None:
        with contextlib.suppress(Aria2Error):
            await self.call("aria2.forceRemove", gid)
        for _ in range(20):  # wait until aria2 has flushed and released the file
            try:
                state = (await self.status(gid)).get("status")
            except Aria2Error:
                break
            if state in ("removed", "complete", "error"):
                break
            await asyncio.sleep(0.1)
        await self.forget(gid)

    async def forget(self, gid: str) -> None:
        with contextlib.suppress(Aria2Error):
            await self.call("aria2.removeDownloadResult", gid)
