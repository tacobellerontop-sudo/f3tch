import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { progressStore } from "@/lib/progress";

import { HttpFailure, local, makeActivity, makeDownload, page, renderApp } from "./harness";

const stats = { by_state: {}, active: 1, queued: 4, completed: 12, failed: 2, speed_bps: 0 };

describe("overview", () => {
  it("renders statistics, active downloads, storage and activity from the API", async () => {
    renderApp("/", local({
      "GET /downloads/stats": stats,
      "GET /downloads": page([makeDownload()]),
      "GET /activity": page([makeActivity()]),
    }));

    const main = within(await screen.findByRole("main"));
    expect(await main.findByText("ubuntu.iso")).toBeInTheDocument();
    expect(main.getByText("12")).toBeInTheDocument();
    expect(main.getByRole("link", { name: /Failed/ })).toHaveAttribute("href", "/downloads?state=failed");
    expect(main.getByText("Downloading")).toBeInTheDocument();
    expect(main.getByText("1.0 GB / 4.0 GB")).toBeInTheDocument();
    expect(main.getByText("25.0%")).toBeInTheDocument();
    expect(main.getAllByText(/5\.0 MB\/s/)).toHaveLength(2); // card and the speed tile
    expect(main.getByText("Downloaded ubuntu.iso")).toBeInTheDocument();
    expect(main.getByText(/of 50 GB/)).toBeInTheDocument();
  });

  it("updates progress from live events without refetching", async () => {
    const { server } = renderApp("/", local({
      "GET /downloads/stats": stats,
      "GET /downloads": page([makeDownload()]),
    }));
    const main = within(await screen.findByRole("main"));
    await main.findByText("25.0%");
    const before = server.calls.length;

    act(() => {
      progressStore.set({ id: 1, downloaded_bytes: 2 * 1024 ** 3, total_bytes: 4 * 1024 ** 3, speed_bps: 8 * 1024 ** 2, avg_speed_bps: 6 * 1024 ** 2, eta_seconds: 256 });
    });

    expect(await main.findByText("50.0%")).toBeInTheDocument();
    expect(main.getByText("2.0 GB / 4.0 GB")).toBeInTheDocument();
    expect(server.calls.length).toBe(before);
  });

  it("shows an empty state with a way to add a download", async () => {
    renderApp("/", local());
    const main = within(await screen.findByRole("main"));
    expect(await main.findByText("Nothing is downloading")).toBeInTheDocument();
    expect(main.getAllByRole("button", { name: "Add download" }).length).toBeGreaterThan(0);
  });

  it("has no sign-in, accounts or user administration", async () => {
    const { server } = renderApp("/", local());
    const nav = within((await screen.findAllByRole("navigation", { name: "Main" }))[0]);
    expect(nav.getByRole("link", { name: "Downloads" })).toBeInTheDocument();
    expect(nav.getByRole("link", { name: "Settings" })).toBeInTheDocument();
    expect(nav.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Account menu" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "Scope" })).not.toBeInTheDocument();
    expect(screen.queryByText(/sign/i)).not.toBeInTheDocument();
    expect(server.calls.some((call) => call.path.startsWith("/auth"))).toBe(false);
  });

  it("sends links from versions with accounts to the overview", async () => {
    for (const path of ["/login", "/setup", "/invite/abc", "/users"]) {
      const view = renderApp(path, local());
      expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
      expect(window.location.pathname).toBe("/");
      view.unmount();
    }
  });

  it("explains a server that cannot be reached", async () => {
    renderApp("/", local({ "GET /bootstrap": new HttpFailure(502, "bad_gateway", "Down.") }));
    expect(await screen.findByText("Cannot reach the FETCH server", {}, { timeout: 5000 })).toBeInTheDocument();
  });
});
