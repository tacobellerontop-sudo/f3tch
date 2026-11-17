"""Loopback CONNECT proxy that applies the SSRF guard to aria2's traffic.

aria2 opens its own connections and follows redirects on its own, so it cannot
be trusted to stay away from internal addresses. FETCH therefore forces every
aria2 connection through this tiny tunnel-only proxy, which resolves the
target itself, refuses private/local addresses, and connects to exactly the
address it validated. The proxy listens on 127.0.0.1 and requires a random
per-run password.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import logging
import secrets
import socket

from ..security.urlguard import UnsafeUrlError, UrlGuard

log = logging.getLogger("fetch.guardproxy")


class GuardProxy:
    def __init__(self, guard: UrlGuard) -> None:
        self._guard = guard
        self._server: asyncio.Server | None = None
        self.username = "fetch"
        self.password = secrets.token_urlsafe(24)
        self.port = 0
        self.denied = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2)
            self._server = None

    async def _reply(self, writer: asyncio.StreamWriter, status: str) -> None:
        with contextlib.suppress(Exception):
            writer.write(f"HTTP/1.1 {status}\r\nConnection: close\r\n\r\n".encode())
            await writer.drain()
            writer.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=15)
            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=15)
                if line in (b"\r\n", b"\n", b""):
                    break
                name, _, value = line.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            parts = request.decode("latin-1").split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                return await self._reply(writer, "405 Method Not Allowed")
            expected = "Basic " + base64.b64encode(
                f"{self.username}:{self.password}".encode()
            ).decode()
            if not hmac.compare_digest(headers.get("proxy-authorization", ""), expected):
                return await self._reply(writer, "407 Proxy Authentication Required")
            host, _, port_text = parts[1].rpartition(":")
            host = host.strip("[]")
            port = int(port_text)
            address = await self._resolve(host, port)
        except (UnsafeUrlError, OSError, ValueError, asyncio.TimeoutError) as exc:
            self.denied += 1
            log.warning("Guard proxy refused a connection", extra={"reason": str(exc)})
            return await self._reply(writer, "403 Forbidden")

        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(address, port), timeout=30
            )
        except (OSError, asyncio.TimeoutError):
            return await self._reply(writer, "502 Bad Gateway")

        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(
            self._pipe(reader, upstream_writer), self._pipe(upstream_reader, writer)
        )

    async def _resolve(self, host: str, port: int) -> str:
        bracketed = f"[{host}]" if ":" in host else host
        self._guard.check_url(f"https://{bracketed}:{port}/")
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        for info in infos:
            address = info[4][0]
            if self._guard.host_allowlisted(host) or self._guard.ip_allowed(address):
                return address
        raise UnsafeUrlError(f"{host} resolves to a private or local address.")

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(256 * 1024)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
