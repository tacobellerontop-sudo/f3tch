"""Download over more than one network connection at once.

A computer with two ways online (two Wi-Fi adapters, or Wi-Fi plus Ethernet or
a tethered phone) can fetch the segments of one file over both. Every adapter
gets its own :class:`HttpClient` whose sockets are bound to that adapter's
local IPv4 address. Windows sends a packet out of the adapter that owns its
source address (the strong host model), so binding is enough to pick the route
as long as each adapter has its own gateway.

Each client counts the bytes it receives (an aiohttp trace hook), and a
sampler turns those counters into per-adapter speeds once a second. The
dashboard draws them live from the ``network.speeds`` event.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
import socket
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .http import HttpClient

log = logging.getLogger("fetch.netpaths")

#: Seconds of speed history kept for the chart.
HISTORY_SECONDS = 90
#: Public addresses used to check that an adapter really reaches the internet.
PROBE_TARGETS: tuple[tuple[str, int], ...] = (("1.1.1.1", 443), ("8.8.8.8", 443))
PROBE_TIMEOUT = 3.0
#: An adapter is probed again after this long.
PROBE_TTL = 60.0

# Adapters that are not a way online of their own.
_VIRTUAL = re.compile(
    r"loopback|vethernet|hyper-v|virtualbox|vmware|vbox|docker|wsl|tailscale|zerotier|"
    r"hamachi|npcap|tap-|tun|wintun|wireguard|vpn|bluetooth|teredo|isatap|pseudo|"
    r"^lo$|^veth|^br-|^virbr",
    re.IGNORECASE,
)


def adapter_kind(name: str) -> str:
    """A rough guess from the adapter's name: wifi, ethernet, cellular or other."""
    lowered = name.lower()
    if re.search(r"wi-?fi|wlan|wireless|802\.11|^wl", lowered):
        return "wifi"
    if re.search(r"cellular|mobile|wwan|ndis|iphone|android|usb|tether", lowered):
        return "cellular"
    if re.search(r"ethernet|^eth|^en|local area connection", lowered):
        return "ethernet"
    return "other"


@dataclass
class Adapter:
    """One network adapter with a usable IPv4 address."""

    id: str  # the adapter name; stable across restarts
    name: str
    address: str
    kind: str
    speed_mbps: int = 0  # link speed reported by the system, 0 if unknown
    online: bool | None = None  # None until probed
    latency_ms: float | None = None
    checked_at: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "kind": self.kind,
            "link_mbps": self.speed_mbps,
            "online": self.online,
            "latency_ms": None if self.latency_ms is None else round(self.latency_ms, 1),
        }


def _usable_ipv4(address: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified)


def discover_adapters() -> list[Adapter]:
    """Adapters that are up and have a routable IPv4 address."""
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a requirement
        log.warning("psutil is missing; FETCH cannot list network adapters")
        return []
    stats = psutil.net_if_stats()
    found: list[Adapter] = []
    for name, addresses in psutil.net_if_addrs().items():
        stat = stats.get(name)
        if stat is not None and not stat.isup:
            continue
        if _VIRTUAL.search(name):
            continue
        for entry in addresses:
            if entry.family == socket.AF_INET and _usable_ipv4(entry.address):
                found.append(
                    Adapter(
                        id=name,
                        name=name,
                        address=entry.address,
                        kind=adapter_kind(name),
                        speed_mbps=int(getattr(stat, "speed", 0) or 0),
                    )
                )
                break
    found.sort(key=lambda a: ({"ethernet": 0, "wifi": 1, "cellular": 2}.get(a.kind, 3), a.name))
    return found


async def probe(address: str, targets: Iterable[tuple[str, int]] = PROBE_TARGETS) -> float | None:
    """Open a TCP connection from ``address``; the round trip in ms, or None."""
    for host, port in targets:
        started = time.perf_counter()
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, local_addr=(address, 0)), PROBE_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError):
            continue
        elapsed = (time.perf_counter() - started) * 1000
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return elapsed
    return None


@dataclass
class Meter:
    """Bytes received over one path, and its speed once a second."""

    total: int = 0
    _last_total: int = 0
    _last_time: float = field(default_factory=time.monotonic)
    bps: float = 0.0
    history: deque[float] = field(default_factory=lambda: deque(maxlen=HISTORY_SECONDS))

    def add(self, count: int) -> None:
        self.total += count

    def sample(self, now: float) -> float:
        elapsed = max(1e-3, now - self._last_time)
        raw = (self.total - self._last_total) / elapsed
        # Light smoothing so the chart does not jitter on bursty chunks.
        self.bps = raw if not self.history else 0.6 * raw + 0.4 * self.bps
        self._last_total, self._last_time = self.total, now
        self.history.append(round(self.bps, 1))
        return self.bps


def meter_trace(meter: Meter) -> aiohttp.TraceConfig:
    """An aiohttp trace hook that adds every received chunk to ``meter``."""
    trace = aiohttp.TraceConfig()

    async def on_chunk(_session, _ctx, params) -> None:
        meter.add(len(params.chunk))

    trace.on_response_chunk_received.append(on_chunk)
    return trace


class BoundHttpClient(HttpClient):
    """An :class:`HttpClient` whose connections leave from one local address."""

    def __init__(self, guard, local_address: str | None, meter: Meter) -> None:
        super().__init__(guard)
        self.local_address = local_address
        self.meter = meter
        self.trace_configs.append(meter_trace(meter))

    def _connector_options(self) -> dict[str, Any]:
        options = super()._connector_options()
        if self.local_address:
            options["local_addr"] = (self.local_address, 0)
            options["family"] = socket.AF_INET
        return options


DEFAULT_PATH = "default"


class NetworkPaths:
    """Adapters, their bound clients and their live speeds."""

    def __init__(
        self,
        guard,
        bus,
        settings: Callable[[], Any],
        *,
        discover: Callable[[], list[Adapter]] = discover_adapters,
        prober: Callable[[str], Any] = probe,
    ) -> None:
        self._guard = guard
        self._bus = bus
        self._settings = settings
        self._discover = discover
        self._probe = prober
        self._adapters: dict[str, Adapter] = {}
        self._clients: dict[str, BoundHttpClient] = {}
        self.meters: dict[str, Meter] = {DEFAULT_PATH: Meter()}
        self.default_trace = meter_trace(self.meters[DEFAULT_PATH])
        self._lock = asyncio.Lock()
        self._sampler: asyncio.Task | None = None
        self._refresher: asyncio.Task | None = None
        self.started_at = time.monotonic()

    # -- settings ----------------------------------------------------------------

    def _config(self) -> tuple[bool, list[str]]:
        network = self._settings().network
        return bool(network.multi_path), list(network.adapters)

    @property
    def enabled(self) -> bool:
        return self._config()[0]

    # -- lifecycle ---------------------------------------------------------------

    async def start(self) -> None:
        # Listing and probing adapters can take a few seconds: never hold up startup.
        self._refresher = asyncio.create_task(
            self.refresh(probe=self.enabled), name="fetch-netpaths-refresh"
        )
        self._sampler = asyncio.create_task(self._sample_loop(), name="fetch-netpaths-sampler")

    async def stop(self) -> None:
        for task in (self._sampler, self._refresher):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    # -- adapters ----------------------------------------------------------------

    async def refresh(self, *, force: bool = False, probe: bool = True) -> list[Adapter]:
        """List adapters again and probe the ones not checked lately."""
        async with self._lock:
            found = await asyncio.to_thread(self._discover)
            now = time.monotonic()
            fresh: dict[str, Adapter] = {}
            for adapter in found:
                old = self._adapters.get(adapter.id)
                if old and old.address == adapter.address:
                    adapter.online, adapter.latency_ms, adapter.checked_at = (
                        old.online, old.latency_ms, old.checked_at,
                    )
                fresh[adapter.id] = adapter
            stale = [
                a for a in fresh.values()
                if probe and (force or a.online is None or now - a.checked_at > PROBE_TTL)
            ]
            results = await asyncio.gather(*(self._probe(a.address) for a in stale))
            for adapter, latency in zip(stale, results):
                adapter.online = latency is not None
                adapter.latency_ms = latency
                adapter.checked_at = now
            # Drop clients whose adapter vanished or changed address.
            for key in list(self._clients):
                if key not in fresh or fresh[key].address != self._clients[key].local_address:
                    await self._clients.pop(key).close()
            self._adapters = fresh
            for key in fresh:
                self.meters.setdefault(key, Meter())
            return list(fresh.values())

    def adapters(self) -> list[Adapter]:
        return list(self._adapters.values())

    def active(self) -> list[Adapter]:
        """The adapters a download should spread over (two or more, or none)."""
        enabled, chosen = self._config()
        if not enabled:
            return []
        usable = [a for a in self._adapters.values() if a.online]
        if chosen:
            usable = [a for a in usable if a.id in chosen]
        return usable if len(usable) >= 2 else []

    def client(self, adapter_id: str) -> BoundHttpClient:
        client = self._clients.get(adapter_id)
        if client is None:
            adapter = self._adapters[adapter_id]
            client = BoundHttpClient(self._guard, adapter.address, self.meters[adapter_id])
            self._clients[adapter_id] = client
        return client

    def client_for(self) -> Callable[[int], HttpClient] | None:
        """A segment-index -> client chooser for one download, or None for one path.

        Segments are handed out round-robin; since every segment worker pulls
        the next range when it finishes (work stealing), the faster adapter
        ends up carrying more of the file.
        """
        adapters = self.active()
        if not adapters:
            return None
        clients = [self.client(a.id) for a in adapters]

        def choose(index: int) -> HttpClient:
            return clients[index % len(clients)]

        return choose

    def mark_failed(self, client: HttpClient) -> None:
        """A bound client could not connect: probe its adapter again soon."""
        for key, value in self._clients.items():
            if value is client and key in self._adapters:
                self._adapters[key].checked_at = 0.0

    # -- speeds ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        enabled, chosen = self._config()
        active = {a.id for a in self.active()}
        paths = []
        for adapter in self._adapters.values():
            meter = self.meters.get(adapter.id, Meter())
            paths.append(
                {
                    **adapter.public(),
                    "selected": (not chosen) or adapter.id in chosen,
                    "active": adapter.id in active,
                    "bps": round(meter.bps, 1),
                    "total_bytes": meter.total,
                    "history": list(meter.history),
                }
            )
        default = self.meters[DEFAULT_PATH]
        return {
            "enabled": enabled,
            "multi_path_active": bool(active),
            "interval_seconds": 1,
            "default": {"bps": round(default.bps, 1), "total_bytes": default.total, "history": list(default.history)},
            "adapters": paths,
            "total_bps": round(sum(m.bps for m in self.meters.values()), 1),
        }

    def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        for meter in self.meters.values():
            meter.sample(now)
        return {
            "ts": time.time(),
            "default_bps": round(self.meters[DEFAULT_PATH].bps, 1),
            "adapters": {key: round(m.bps, 1) for key, m in self.meters.items() if key != DEFAULT_PATH},
            "total_bps": round(sum(m.bps for m in self.meters.values()), 1),
        }

    async def _sample_loop(self) -> None:
        tick = 0
        while True:
            await asyncio.sleep(1)
            tick += 1
            try:
                data = self.sample()
                if self._bus.subscriber_count:
                    self._bus.publish("network.speeds", data, audience="all")
                if tick % 30 == 0 and self.enabled:
                    before = {a.id: (a.address, a.online) for a in self._adapters.values()}
                    await self.refresh()
                    after = {a.id: (a.address, a.online) for a in self._adapters.values()}
                    if before != after:
                        self._bus.publish("network.changed", {}, audience="all")
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - keep sampling no matter what
                log.exception("network sampler failed")
