import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { ApiError, api, buildUrl, errorMessage, setCsrfToken } from "@/lib/api";
import { formatBytes, formatDuration, formatPercent, formatRelative, formatSpeed, initials } from "@/lib/format";
import { progressStore } from "@/lib/progress";
import { applyServerEvent } from "@/lib/realtime";
import type { Download, Page } from "@/lib/types";

import { makeDownload, mockApi, HttpFailure } from "./harness";

describe("formatting", () => {
  it("formats sizes, speeds and durations", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
    expect(formatBytes(null)).toBe("—");
    expect(formatSpeed(0)).toBe("—");
    expect(formatSpeed(2 * 1024 ** 2)).toBe("2.0 MB/s");
    expect(formatDuration(59)).toBe("59s");
    expect(formatDuration(125)).toBe("2m 05s");
    expect(formatDuration(3 * 3600 + 120)).toBe("3h 02m");
    expect(formatDuration(null)).toBe("—");
  });

  it("never invents a percentage when the size is unknown", () => {
    expect(formatPercent(500, null)).toBeNull();
    expect(formatPercent(500, 0)).toBeNull();
    expect(formatPercent(50, 200)).toBe(25);
    expect(formatPercent(300, 200)).toBe(100);
  });

  it("formats relative times and initials", () => {
    const now = Date.parse("2026-01-10T12:00:00Z");
    expect(formatRelative("2026-01-10T11:59:58Z", now)).toBe("just now");
    expect(formatRelative("2026-01-10T11:55:00Z", now)).toBe("5m ago");
    expect(formatRelative("2026-01-10T14:00:00Z", now)).toBe("in 2h");
    expect(initials("alice.smith")).toBe("AS");
    expect(initials("")).toBe("?");
  });
});

describe("API client", () => {
  it("builds query strings without empty values", () => {
    expect(buildUrl("/downloads", { state: "failed", q: "", page: 2, owner: null })).toBe("/api/downloads?state=failed&page=2");
  });

  it("adds the CSRF token to writes only and surfaces server error messages", async () => {
    const server = mockApi({
      "GET /downloads": { items: [] },
      "POST /downloads/1/pause": new HttpFailure(409, "invalid_state", "A completed download cannot be paused."),
    });
    setCsrfToken("tok");
    await api.get("/downloads");
    expect(server.calls[0].headers["X-CSRF-Token"]).toBeUndefined();
    const failure = await api.post("/downloads/1/pause").catch((error: unknown) => error);
    expect(server.calls[1].headers["X-CSRF-Token"]).toBe("tok");
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).code).toBe("invalid_state");
    expect(errorMessage(failure)).toBe("A completed download cannot be paused.");
  });

  it("reports an unreachable server as a network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const failure = await api.get("/bootstrap").catch((error: unknown) => error);
    expect((failure as ApiError).code).toBe("network_error");
  });
});

describe("real-time events", () => {
  function seeded() {
    const client = new QueryClient();
    const key = ["downloads", "list", { page: 1 }];
    client.setQueryData<Page<Download>>(key, { items: [makeDownload()], total: 1, page: 1, page_size: 50 });
    return { client, key, invalidate: vi.fn() };
  }

  it("stores progress without touching the query cache", () => {
    const { client, key, invalidate } = seeded();
    const before = client.getQueryData(key);
    applyServerEvent(client, { type: "download.progress", data: { id: 1, downloaded_bytes: 10, total_bytes: 100, speed_bps: 5, avg_speed_bps: 5, eta_seconds: 18 } }, invalidate);
    expect(progressStore.get(1)?.downloaded_bytes).toBe(10);
    expect(client.getQueryData(key)).toBe(before);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("patches a download in place when its state changes", () => {
    const { client, key, invalidate } = seeded();
    applyServerEvent(client, { type: "download.paused", data: { download: makeDownload({ state: "paused" }) } }, invalidate);
    expect(client.getQueryData<Page<Download>>(key)?.items[0].state).toBe("paused");
  });

  it("removes a deleted download and refreshes storage when one completes", () => {
    const { client, key, invalidate } = seeded();
    applyServerEvent(client, { type: "download.completed", data: { download: makeDownload({ state: "completed" }) } }, invalidate);
    expect(invalidate).toHaveBeenCalledWith("files");
    applyServerEvent(client, { type: "download.removed", data: { id: 1 } }, invalidate);
    expect(client.getQueryData<Page<Download>>(key)?.items).toHaveLength(0);
    expect(progressStore.get(1)).toBeUndefined();
  });

  it("refreshes the right data for other events", () => {
    const { client, invalidate } = seeded();
    applyServerEvent(client, { type: "providers.changed", data: {} }, invalidate);
    applyServerEvent(client, { type: "users.changed", data: {} }, invalidate);
    applyServerEvent(client, { type: "settings.changed", data: {} }, invalidate);
    const keys = invalidate.mock.calls.map((call) => call[0]);
    expect(keys).toEqual(expect.arrayContaining(["providers", "users", "settings"]));
  });
});
