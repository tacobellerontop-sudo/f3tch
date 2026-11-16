"""SSRF protection for every outbound request made on a user's behalf.

Two layers:

* :meth:`UrlGuard.check_url` validates the scheme and, for literal IP hosts,
  the address itself. It is called for the submitted URL and for every
  redirect hop.
* :class:`GuardedResolver` is installed into the aiohttp connector, so the
  address a hostname resolves to is validated *at connect time* and the
  connection is made to exactly that address (no DNS-rebinding window).
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver

ALLOWED_SCHEMES = {"http", "https"}
MAX_URL_LENGTH = 4096
_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")


class UnsafeUrlError(ValueError):
    """The URL points somewhere FETCH refuses to connect to."""


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class UrlGuard:
    def __init__(self, allowlist: list[str] | None = None) -> None:
        self._networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        self._hosts: set[str] = set()
        for entry in allowlist or []:
            entry = entry.strip().lower()
            if not entry:
                continue
            try:
                self._networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                self._hosts.add(entry)

    def host_allowlisted(self, host: str) -> bool:
        return host.lower().rstrip(".") in self._hosts

    def ip_allowed(self, address: str) -> bool:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return False
        if any(ip in net for net in self._networks):
            return True
        return _is_public(ip)

    def check_url(self, url: str) -> str:
        """Validate ``url`` and return it normalised. Raises :class:`UnsafeUrlError`."""
        url = (url or "").strip()
        if not url or len(url) > MAX_URL_LENGTH:
            raise UnsafeUrlError("The URL is empty or too long.")
        if any(ord(ch) < 0x20 or ch == "\x7f" for ch in url):
            raise UnsafeUrlError("The URL contains control characters.")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise UnsafeUrlError("The URL is malformed.") from exc
        if parts.scheme.lower() not in ALLOWED_SCHEMES:
            raise UnsafeUrlError("Only http and https links are supported.")
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            raise UnsafeUrlError("The URL has no host.")
        if port is not None and not (0 < port < 65536):
            raise UnsafeUrlError("The URL has an invalid port.")
        if self.host_allowlisted(host):
            return url
        try:
            ipaddress.ip_address(host)
            is_literal = True
        except ValueError:
            is_literal = False
        if is_literal:
            if not self.ip_allowed(host):
                raise UnsafeUrlError("Links to private or local network addresses are not allowed.")
        else:
            if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES) or "." not in host:
                raise UnsafeUrlError("Links to local network hosts are not allowed.")
        return url


class GuardedResolver(AbstractResolver):
    """Resolver that drops every address the guard does not allow."""

    def __init__(self, guard: UrlGuard) -> None:
        self._guard = guard
        self._inner = DefaultResolver()

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[dict[str, Any]]:
        results = await self._inner.resolve(host, port, family)
        if self._guard.host_allowlisted(host):
            return results
        allowed = [r for r in results if self._guard.ip_allowed(r["host"])]
        if not allowed:
            raise OSError(f"{host} resolves to a private or local address; connection refused.")
        return allowed

    async def close(self) -> None:
        await self._inner.close()
