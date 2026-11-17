import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { parseUrls } from "@/components/downloads/AddDownloadDialog";
import type { LinkInfo } from "@/lib/types";

import { type Call, HttpFailure, makeDownload, makeMe, page, renderApp, signedIn } from "./harness";

function inspected(url: string, overrides: Partial<LinkInfo> = {}): LinkInfo {
  return {
    url, provider: "direct", provider_name: "Direct HTTP", supported: true, filename: url.split("/").pop() ?? "",
    size: 1024 ** 2, resumable: true, restriction: "", message: "", ...overrides,
  };
}

describe("parseUrls", () => {
  it("splits on whitespace and commas and removes duplicates", () => {
    expect(parseUrls(" https://a.example/1\nhttps://b.example/2, https://a.example/1\n\n")).toEqual([
      "https://a.example/1",
      "https://b.example/2",
    ]);
  });
});

describe("adding downloads", () => {
  it("inspects pasted links, shows why one is refused and submits only the accepted one", async () => {
    const good = "https://files.example.com/movie.mkv";
    const bad = "https://mega.nz/folder/abc#def";
    const { user, server } = renderApp("/downloads", signedIn(makeMe("user"), {
      "POST /downloads/inspect": (call: Call) => ({
        items: (call.body as { urls: string[] }).urls.map((url) =>
          url === bad
            ? inspected(url, { provider: "mega", supported: false, filename: "", size: null, message: "MEGA folder links are not supported." })
            : inspected(url),
        ),
      }),
      "POST /downloads": { created: [makeDownload({ id: 5, filename: "movie.mkv", state: "queued" })], rejected: [] },
    }));

    const main = within(await screen.findByRole("main"));
    await user.click((await main.findAllByRole("button", { name: "Add download" }))[0]);
    const dialog = within(await screen.findByRole("dialog"));
    await user.click(dialog.getByLabelText("Links"));
    await user.paste(`${good}\n${bad}`);

    expect(await dialog.findByText("movie.mkv")).toBeInTheDocument();
    expect(dialog.getByText("MEGA folder links are not supported.")).toBeInTheDocument();
    expect(dialog.getByText(/1 ready · 1 cannot be added/)).toBeInTheDocument();

    await user.click(dialog.getByRole("button", { name: "Add download" }));
    await waitFor(() => expect(server.find("POST", "/downloads")).toHaveLength(1));
    const call = server.find("POST", "/downloads")[0];
    expect(call.body).toEqual({ urls: [good], destination: "private", folder: "", priority: 1 });
    expect(call.headers["X-CSRF-Token"]).toBe("csrf-test-token");
    expect(await screen.findByText("1 download added to the queue.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("keeps the dialog open and reports links the server rejected (for example over quota)", async () => {
    const url = "https://files.example.com/huge.bin";
    const { user } = renderApp("/downloads", signedIn(makeMe("user"), {
      "POST /downloads/inspect": { items: [inspected(url)] },
      "POST /downloads": { created: [], rejected: [{ url, provider: "direct", code: "quota_exceeded", message: "Your storage quota would be exceeded." }] },
    }));
    const main = within(await screen.findByRole("main"));
    await user.click((await main.findAllByRole("button", { name: "Add download" }))[0]);
    const dialog = within(await screen.findByRole("dialog"));
    await user.click(dialog.getByLabelText("Links"));
    await user.paste(url);
    await dialog.findByText("huge.bin");
    await user.click(dialog.getByRole("button", { name: "Add download" }));
    expect(await dialog.findByText("Your storage quota would be exceeded.")).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  it("cannot be submitted while no link is acceptable", async () => {
    const { user } = renderApp("/downloads", signedIn(makeMe("user"), {
      "POST /downloads/inspect": { items: [inspected("ftp://x/y", { supported: false, message: "Only http and https links are supported." })] },
    }));
    const main = within(await screen.findByRole("main"));
    await user.click((await main.findAllByRole("button", { name: "Add download" }))[0]);
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByRole("button", { name: "Add download" })).toBeDisabled();
    await user.click(dialog.getByLabelText("Links"));
    await user.paste("ftp://x/y");
    expect(await dialog.findByText("Only http and https links are supported.")).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Add download" })).toBeDisabled();
  });
});

describe("download controls", () => {
  const rows = [
    makeDownload({ id: 1, filename: "running.iso", state: "downloading" }),
    makeDownload({ id: 2, filename: "paused.zip", state: "paused" }),
    makeDownload({ id: 3, filename: "broken.rar", state: "failed", error_code: "not_found", error_message: "The file was not found (it may have been removed)." }),
    makeDownload({ id: 4, filename: "done.mkv", state: "completed", downloaded_bytes: 4 * 1024 ** 3, final_path: "movies/done.mkv" }),
  ];

  it("lists downloads with their state and error, and asks the API with the filters from the URL", async () => {
    const { server } = renderApp("/downloads?state=active&provider=direct", signedIn(makeMe("user"), { "GET /downloads": page(rows) }));
    expect(await screen.findByText("running.iso")).toBeInTheDocument();
    expect(screen.getAllByText("The file was not found (it may have been removed).").length).toBeGreaterThan(0);
    const query = server.find("GET", "/downloads")[0].query;
    expect(query.get("state")).toBe("inspecting,downloading,verifying,waiting");
    expect(query.get("provider")).toBe("direct");
    expect(query.get("owner")).toBe("me");
  });

  it("pauses, resumes and retries with one click", async () => {
    const { user, server } = renderApp("/downloads", signedIn(makeMe("user"), {
      "GET /downloads": page(rows),
      "POST /downloads/1/pause": { ...rows[0], state: "paused" },
      "POST /downloads/2/resume": { ...rows[1], state: "queued" },
      "POST /downloads/3/retry": { ...rows[2], state: "queued" },
    }));
    await user.click(await screen.findByRole("button", { name: "Pause running.iso" }));
    await user.click(screen.getByRole("button", { name: "Resume paused.zip" }));
    await user.click(screen.getByRole("button", { name: "Retry broken.rar" }));
    await waitFor(() => expect(server.find("POST", "/downloads/3/retry")).toHaveLength(1));
    expect(server.find("POST", "/downloads/1/pause")).toHaveLength(1);
    expect(server.find("POST", "/downloads/2/resume")).toHaveLength(1);
    // A finished download offers no pause / resume / retry.
    expect(screen.queryByRole("button", { name: /^(Pause|Resume|Retry) done\.mkv/ })).not.toBeInTheDocument();
  });

  it("asks for confirmation before cancelling", async () => {
    const { user, server } = renderApp("/downloads", signedIn(makeMe("user"), {
      "GET /downloads": page(rows),
      "POST /downloads/1/cancel": { ...rows[0], state: "cancelled" },
    }));
    await user.click(await screen.findByRole("button", { name: "More actions for running.iso" }));
    await user.click(screen.getByRole("menuitem", { name: "Cancel" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(/data\s+downloaded so far will be deleted/)).toBeInTheDocument();
    expect(server.find("POST", "/downloads/1/cancel")).toHaveLength(0);
    await user.click(dialog.getByRole("button", { name: "Cancel download" }));
    await waitFor(() => expect(server.find("POST", "/downloads/1/cancel")).toHaveLength(1));
  });

  it("removes a finished download and deletes its file only when asked", async () => {
    const { user, server } = renderApp("/downloads", signedIn(makeMe("user"), {
      "GET /downloads": page(rows),
      "DELETE /downloads/4": { ok: true },
    }));
    await user.click(await screen.findByRole("button", { name: "More actions for done.mkv" }));
    await user.click(screen.getByRole("menuitem", { name: "Remove from history" }));
    const dialog = within(await screen.findByRole("dialog"));
    await user.click(dialog.getByLabelText("Also delete the downloaded file"));
    await user.click(dialog.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(server.find("DELETE", "/downloads/4")).toHaveLength(1));
    expect(server.find("DELETE", "/downloads/4")[0].query.get("delete_file")).toBe("true");
  });

  it("applies bulk actions only to the selected downloads that allow them", async () => {
    const { user, server } = renderApp("/downloads", signedIn(makeMe("user"), {
      "GET /downloads": page(rows),
      "POST /downloads/bulk": (call: Call) => ({ done: (call.body as { ids: number[] }).ids, skipped: [] }),
    }));
    await user.click(await screen.findByLabelText("Select all on this page"));
    const toolbar = within(screen.getByRole("toolbar", { name: "Bulk actions" }));
    expect(toolbar.getByText("4 selected")).toBeInTheDocument();
    await user.click(toolbar.getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(server.find("POST", "/downloads/bulk")).toHaveLength(1));
    expect(server.find("POST", "/downloads/bulk")[0].body).toEqual({ ids: [1], action: "pause" });
    expect(await screen.findByText("Paused 1 download.")).toBeInTheDocument();
  });

  it("shows the server's reason when an action is refused", async () => {
    const { user } = renderApp("/downloads", signedIn(makeMe("user"), {
      "GET /downloads": page(rows),
      "POST /downloads/1/pause": new HttpFailure(409, "invalid_state", "A completed download cannot be paused."),
    }));
    await user.click(await screen.findByRole("button", { name: "Pause running.iso" }));
    expect(await screen.findByText("A completed download cannot be paused.")).toBeInTheDocument();
  });

  it("lets the user complete a provider CAPTCHA themselves and sends exactly what they typed", async () => {
    const waiting = makeDownload({
      id: 9, filename: "archive.rar", provider: "rapidgator", state: "waiting", error_code: "captcha_required",
      wait_reason: "Waiting for you to complete the provider's CAPTCHA.", wait_until: new Date(Date.now() + 300_000).toISOString(),
    });
    const { user, server } = renderApp("/downloads", signedIn(makeMe("user"), {
      "GET /downloads": page([waiting]),
      "GET /downloads/9/captcha": {
        download_id: 9, provider: "rapidgator", kind: "image", prompt: "Type the characters from Rapidgator's picture.",
        has_image: true, page_url: "https://rapidgator.net/file/abc", accepts_download_url: true, attempt: 1, expires_at: null,
      },
      "POST /downloads/9/captcha": { ...waiting, state: "queued" },
    }));
    await user.click(await screen.findByText("archive.rar"));
    await user.click(await screen.findByRole("button", { name: "Complete CAPTCHA" }));
    expect(await screen.findByText("Type the characters from Rapidgator's picture.")).toBeInTheDocument();
    expect(screen.getByAltText("CAPTCHA picture from the provider")).toHaveAttribute("src", "/api/downloads/9/captcha/image?attempt=1");
    expect(server.find("POST", "/downloads/9/captcha")).toHaveLength(0);
    await user.type(screen.getByLabelText("Characters in the picture"), "k7xw2");
    await user.click(screen.getByRole("button", { name: "Continue download" }));
    await waitFor(() => expect(server.find("POST", "/downloads/9/captcha")).toHaveLength(1));
    expect(server.find("POST", "/downloads/9/captcha")[0].body).toEqual({ answer: "k7xw2" });
  });

  it("reorders the queue by sending the new order of that priority group", async () => {
    const queued = [
      makeDownload({ id: 1, filename: "first.bin", state: "queued", priority: 1 }),
      makeDownload({ id: 2, filename: "second.bin", state: "queued", priority: 1 }),
      makeDownload({ id: 3, filename: "third.bin", state: "paused", priority: 1 }),
    ];
    const { user, server } = renderApp("/queue", signedIn(makeMe("user"), {
      "GET /downloads": (call: Call) => page(call.query.get("sort") === "queue" ? queued : []),
      "POST /downloads/reorder": { ok: true },
      "PATCH /downloads/2": { ...queued[1], priority: 2 },
    }));
    expect(await screen.findByText("third.bin")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move first.bin up" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Move third.bin to the top" }));
    await waitFor(() => expect(server.find("POST", "/downloads/reorder")).toHaveLength(1));
    expect(server.find("POST", "/downloads/reorder")[0].body).toEqual({ ids: [3, 1, 2] });

    const priority = screen.getByLabelText("Priority of second.bin");
    expect(within(priority).queryByRole("option", { name: "Urgent" })).not.toBeInTheDocument();
    await user.selectOptions(priority, "High");
    await waitFor(() => expect(server.find("PATCH", "/downloads/2")).toHaveLength(1));
    expect(server.find("PATCH", "/downloads/2")[0].body).toEqual({ priority: 2 });
  });
});
