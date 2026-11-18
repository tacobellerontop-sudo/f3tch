import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { speedStore, type NetworkSnapshot } from "@/lib/netspeed";

import { local, renderApp } from "./harness";

const MB = 1024 * 1024;

function snapshot(overrides: Partial<NetworkSnapshot> = {}): NetworkSnapshot {
  return {
    enabled: true,
    multi_path_active: true,
    interval_seconds: 1,
    default: { bps: 0, total_bytes: 0, history: [] },
    adapters: [
      { id: "Wi-Fi", name: "Wi-Fi", address: "192.168.1.20", kind: "wifi", link_mbps: 0, online: true, latency_ms: 14, selected: true, active: true, bps: 0, total_bytes: 0, history: [] },
      { id: "Wi-Fi 2", name: "Wi-Fi 2", address: "192.168.43.7", kind: "wifi", link_mbps: 0, online: true, latency_ms: 31, selected: true, active: true, bps: 0, total_bytes: 0, history: [] },
    ],
    total_bps: 0,
    ...overrides,
  };
}

describe("network speed panel", () => {
  it("shows each connection and follows live speed samples", async () => {
    renderApp("/", local({ "GET /network": snapshot() }));
    const main = within(await screen.findByRole("main"));
    const lanes = within(await main.findByRole("list", { name: "Connections" }));
    expect(lanes.getByText("Wi-Fi")).toBeInTheDocument();
    expect(lanes.getByText("Wi-Fi 2")).toBeInTheDocument();
    expect(main.getByText("Downloading over 2 connections at once")).toBeInTheDocument();
    expect(main.getByRole("switch", { name: /Use every connection at once/ })).toBeChecked();

    act(() => {
      speedStore.push({ ts: 1, default_bps: 0, adapters: { "Wi-Fi": 6 * MB, "Wi-Fi 2": 2 * MB }, total_bps: 8 * MB });
    });
    expect(await main.findByText("8.0 MB/s")).toBeInTheDocument();
    expect(lanes.getByText("6.0 MB/s")).toBeInTheDocument();
    expect(lanes.getByText(/^75%/)).toBeInTheDocument();
    expect(lanes.getByText(/^25%/)).toBeInTheDocument();
  });

  it("explains how to get a second connection when there is one", async () => {
    const one = snapshot({ enabled: false, multi_path_active: false });
    one.adapters = one.adapters.slice(0, 1);
    renderApp("/", local({ "GET /network": one }));
    const main = within(await screen.findByRole("main"));
    expect(await main.findByText(/Connect a second Wi-Fi adapter/)).toBeInTheDocument();
    expect(main.getByText("Usual connection")).toBeInTheDocument();
  });
});
