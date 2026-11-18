"""Downloads over more than one network connection, and the live speed view."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from aiohttp import web

from fetch_app.engine.netpaths import (
    DEFAULT_PATH,
    Adapter,
    BoundHttpClient,
    Meter,
    NetworkPaths,
    adapter_kind,
)
from fetch_app.events import EventBus
from fetch_app.security.urlguard import UrlGuard

from .conftest import Api, make_boot
from fetch_app.app import create_app


def test_adapter_kind_guesses_from_the_name() -> None:
    assert adapter_kind("Wi-Fi") == "wifi"
    assert adapter_kind("Wi-Fi 2") == "wifi"
    assert adapter_kind("wlan0") == "wifi"
    assert adapter_kind("Ethernet") == "ethernet"
    assert adapter_kind("eth0") == "ethernet"
    assert adapter_kind("Cellular") == "cellular"
    assert adapter_kind("Remote NDIS based Internet Sharing Device") == "cellular"
    assert adapter_kind("Something") == "other"


def test_meter_turns_bytes_into_speed() -> None:
    meter = Meter()
    start = meter._last_time
    meter.add(1000)
    assert meter.sample(start + 1.0) == pytest.approx(1000)
    meter.add(3000)
    # Smoothed: 0.6 * 3000 + 0.4 * 1000
    assert meter.sample(start + 2.0) == pytest.approx(2200)
    assert list(meter.history) == [1000.0, 2200.0]


@pytest.fixture
async def echo_server():
    """A server that answers with the address the request came from."""

    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=request.remote or "", headers={"X-Pad": "x"})

    async def blob(request: web.Request) -> web.Response:
        return web.Response(body=b"z" * 200_000)

    app = web.Application()
    app.router.add_get("/who", handler)
    app.router.add_get("/blob", blob)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield port
    await runner.cleanup()


async def test_bound_client_leaves_from_its_adapter_and_is_metered(echo_server) -> None:
    guard = UrlGuard(["127.0.0.1"])
    meters = {}
    seen = {}
    for local in ("127.0.0.1", "127.0.0.2"):
        meters[local] = Meter()
        client = BoundHttpClient(guard, local, meters[local])
        try:
            async with client.request("GET", f"http://127.0.0.1:{echo_server}/who") as resp:
                seen[local] = await resp.text()
            async with client.request("GET", f"http://127.0.0.1:{echo_server}/blob") as resp:
                assert len(await resp.read()) == 200_000
        finally:
            await client.close()
    assert seen == {"127.0.0.1": "127.0.0.1", "127.0.0.2": "127.0.0.2"}
    for meter in meters.values():
        assert meter.total >= 200_000


def _settings(enabled: bool, adapters: list[str] | None = None):
    network = SimpleNamespace(multi_path=enabled, adapters=adapters or [])
    return lambda: SimpleNamespace(network=network)


def _discover():
    return [
        Adapter(id="Wi-Fi", name="Wi-Fi", address="127.0.0.1", kind="wifi"),
        Adapter(id="Wi-Fi 2", name="Wi-Fi 2", address="127.0.0.2", kind="wifi"),
        Adapter(id="Ethernet", name="Ethernet", address="127.0.0.3", kind="ethernet"),
    ]


async def _prober(address: str):
    return None if address == "127.0.0.3" else 12.0  # Ethernet is unplugged


async def test_paths_spread_segments_over_online_adapters() -> None:
    paths = NetworkPaths(UrlGuard([]), EventBus(), _settings(True), discover=_discover, prober=_prober)
    await paths.refresh()
    assert [a.id for a in paths.active()] == ["Wi-Fi", "Wi-Fi 2"]
    fallback = object()
    choose = paths.client_for(fallback)
    assert choose is not None
    picks = [choose(i).local_address for i in range(5)]
    assert picks == ["127.0.0.1", "127.0.0.2", "127.0.0.1", "127.0.0.2", "127.0.0.1"]
    # Wi-Fi 2 drops: its connections move to Wi-Fi, then to the usual route.
    paths._adapters["Wi-Fi 2"].online = False
    assert {choose(i).local_address for i in range(4)} == {"127.0.0.1"}
    paths._adapters["Wi-Fi"].online = False
    assert choose(0) is fallback
    paths._adapters["Wi-Fi"].online = paths._adapters["Wi-Fi 2"].online = True
    snap = paths.snapshot()
    assert snap["multi_path_active"] is True
    by_id = {a["id"]: a for a in snap["adapters"]}
    assert by_id["Ethernet"]["online"] is False and by_id["Ethernet"]["active"] is False
    assert by_id["Wi-Fi"]["latency_ms"] == 12.0
    await paths.stop()


async def test_paths_need_two_usable_adapters_and_respect_the_choice() -> None:
    off = NetworkPaths(UrlGuard([]), EventBus(), _settings(False), discover=_discover, prober=_prober)
    await off.refresh()
    assert off.client_for(None) is None

    one = NetworkPaths(
        UrlGuard([]), EventBus(), _settings(True, ["Wi-Fi", "Ethernet"]),
        discover=_discover, prober=_prober,
    )
    await one.refresh()
    assert one.active() == []  # Ethernet is offline, so only one path is left
    assert one.client_for(None) is None


async def test_sample_reports_per_adapter_speeds() -> None:
    paths = NetworkPaths(UrlGuard([]), EventBus(), _settings(True), discover=_discover, prober=_prober)
    await paths.refresh()
    paths.meters["Wi-Fi"].add(5000)
    paths.meters[DEFAULT_PATH].add(1000)
    sample = paths.sample()
    assert sample["adapters"]["Wi-Fi"] > 0
    assert sample["adapters"]["Wi-Fi 2"] == 0
    assert sample["total_bps"] == pytest.approx(sample["adapters"]["Wi-Fi"] + sample["default_bps"])


def test_network_api_and_setting(tmp_path) -> None:
    app = create_app(make_boot(tmp_path))
    with Api(app) as api:
        body = api.get("/network").json()
        assert body["enabled"] is False
        assert "adapters" in body and "default" in body
        changed = api.put("/settings", json={"network": {"multi_path": True, "adapters": ["Wi-Fi"]}})
        assert changed.status_code == 200, changed.text
        assert changed.json()["settings"]["network"]["multi_path"] is True
        assert app.state.ctx.netpaths.enabled is True


def test_download_is_split_over_two_adapters(app, admin, fileserver, boot) -> None:
    import os

    paths = app.state.ctx.netpaths
    paths._discover = lambda: [
        Adapter(id="Wi-Fi", name="Wi-Fi", address="127.0.0.1", kind="wifi"),
        Adapter(id="Wi-Fi 2", name="Wi-Fi 2", address="127.0.0.2", kind="wifi"),
    ]

    async def online(_address):
        return 5.0

    paths._probe = online
    admin.put("/settings", json={"network": {"multi_path": True}, "downloads": {"use_aria2": True}})
    network = admin.post("/network/refresh").json()
    assert network["multi_path_active"] is True

    data = fileserver.add("two.bin", os.urandom(8 * 1024 * 1024))
    job_id = admin.submit(fileserver.url("file", "two.bin"))["created"][0]["id"]
    done = admin.wait_state(job_id, "completed", timeout=60)
    assert done["engine"] == "segmented"
    stored = boot.storage_root / "downloads" / str(admin.user["id"]) / "two.bin"
    assert stored.read_bytes() == data
    sources = {address for address, rng in fileserver.clients if rng}
    assert sources == {"127.0.0.1", "127.0.0.2"}, fileserver.clients

    snap = admin.get("/network").json()
    by_id = {a["id"]: a for a in snap["adapters"]}
    assert by_id["Wi-Fi"]["total_bytes"] > 0 and by_id["Wi-Fi 2"]["total_bytes"] > 0
    assert by_id["Wi-Fi"]["total_bytes"] + by_id["Wi-Fi 2"]["total_bytes"] >= len(data)
