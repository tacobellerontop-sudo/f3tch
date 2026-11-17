import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { progressStore } from "@/lib/progress";

import { makeActivity, makeDownload, makeMe, page, renderApp, signedIn } from "./harness";

const stats = { by_state: {}, active: 1, queued: 4, completed: 12, failed: 2, speed_bps: 0 };

describe("overview", () => {
  it("renders statistics, active downloads, storage and activity from the API", async () => {
    renderApp("/", signedIn(makeMe("user"), {
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
    const { server } = renderApp("/", signedIn(makeMe("user"), {
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
    renderApp("/", signedIn(makeMe("user")));
    const main = within(await screen.findByRole("main"));
    expect(await main.findByText("Nothing is downloading")).toBeInTheDocument();
    expect(main.getAllByRole("button", { name: "Add download" }).length).toBeGreaterThan(0);
  });

  it("hides administration from regular users and shows it to administrators", async () => {
    const first = renderApp("/", signedIn(makeMe("user")));
    const nav = within((await screen.findAllByRole("navigation", { name: "Main" }))[0]);
    expect(nav.getByRole("link", { name: "Downloads" })).toBeInTheDocument();
    expect(nav.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "Scope" })).not.toBeInTheDocument();
    first.unmount();

    renderApp("/", signedIn(makeMe("admin")));
    const adminNav = within((await screen.findAllByRole("navigation", { name: "Main" }))[0]);
    expect(adminNav.getByRole("link", { name: "Users" })).toBeInTheDocument();
    expect(await screen.findByRole("radiogroup", { name: "Scope" })).toBeInTheDocument();
  });
});
