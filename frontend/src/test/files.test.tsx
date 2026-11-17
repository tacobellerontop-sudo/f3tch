import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { type Call, local, makeFile, renderApp } from "./harness";

const listing = (items: ReturnType<typeof makeFile>[], extra: Record<string, unknown> = {}) => ({
  scope: "private", path: "", items, total: items.length, page: 1, page_size: 1000, ...extra,
});

const root = [
  makeFile({ name: "movies", path: "movies", is_dir: true, size: 0, type: "folder", provider: "", downloaded_at: null }),
  makeFile(),
];

describe("file browser", () => {
  it("lists files with a same-origin download link and opens folders", async () => {
    const { user, server } = renderApp("/files", local({
      "GET /files": (call: Call) =>
        call.query.get("path") === "movies"
          ? listing([makeFile({ name: "film.mkv", path: "movies/film.mkv", type: "video" })], { path: "movies" })
          : listing(root),
    }));

    const link = await screen.findByRole("link", { name: "Save a copy of ubuntu.iso" });
    expect(link).toHaveAttribute("href", "/api/files/download?path=ubuntu.iso&scope=private");
    expect(screen.getAllByText("4.0 GB").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "movies" }));
    expect(await screen.findByText("film.mkv")).toBeInTheDocument();
    expect(window.location.search).toContain("path=movies");
    expect(screen.getByRole("link", { name: "Save a copy of film.mkv" })).toHaveAttribute(
      "href",
      "/api/files/download?path=movies%2Ffilm.mkv&scope=private",
    );
    const crumbs = within(screen.getByRole("navigation", { name: "Folder path" }));
    await user.click(crumbs.getByRole("button", { name: "Files" }));
    expect(await screen.findByText("ubuntu.iso")).toBeInTheDocument();
    expect(server.find("GET", "/files").some((call) => call.query.get("path") === "movies")).toBe(true);
  });

  it("deletes a file only after confirmation", async () => {
    const { user, server } = renderApp("/files", local({
      "GET /files": listing(root),
      "DELETE /files": { ok: true },
    }));
    await user.click(await screen.findByRole("button", { name: "Delete ubuntu.iso" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(/permanently deleted/)).toBeInTheDocument();
    expect(server.find("DELETE", "/files")).toHaveLength(0);
    await user.click(dialog.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(server.find("DELETE", "/files")).toHaveLength(1));
    const call = server.find("DELETE", "/files")[0];
    expect(call.query.get("path")).toBe("ubuntu.iso");
    expect(call.query.get("scope")).toBe("private");
  });

  it("creates a folder in the current location", async () => {
    const { user, server } = renderApp("/files?path=movies", local({
      "GET /files": listing([], { path: "movies" }),
      "POST /files/folder": { ok: true, name: "2026" },
    }));
    await user.click(await screen.findByRole("button", { name: "New folder" }));
    await user.type(screen.getByLabelText("Folder name"), "2026");
    await user.click(screen.getByRole("button", { name: "Create folder" }));
    await waitFor(() => expect(server.find("POST", "/files/folder")).toHaveLength(1));
    expect(server.find("POST", "/files/folder")[0].body).toEqual({ scope: "private", path: "movies", name: "2026" });
  });

  it("shows a file or the current folder in the computer's own file manager", async () => {
    const { user, server } = renderApp("/files?path=movies", local({
      "GET /files": listing([makeFile({ name: "film.mkv", path: "movies/film.mkv", type: "video" })], { path: "movies" }),
      "POST /files/reveal": { ok: true },
    }));
    await user.click(await screen.findByRole("button", { name: "Show film.mkv in its folder" }));
    await user.click(screen.getByRole("button", { name: "Open folder" }));
    await waitFor(() => expect(server.find("POST", "/files/reveal")).toHaveLength(2));
    expect(server.find("POST", "/files/reveal").map((call) => call.body)).toEqual([
      { scope: "private", path: "movies/film.mkv" },
      { scope: "private", path: "movies" },
    ]);
  });

  it("keeps the shared folder of earlier versions reachable while it holds files", async () => {
    const { user } = renderApp("/files", local({
      "GET /files/usage": { private: { used_bytes: 10, limit_bytes: 0 }, shared: { used_bytes: 4096 }, disk: { total: 1, used: 0, free: 1 } },
      "GET /files": (call: Call) =>
        call.query.get("scope") === "shared" ? listing([makeFile({ name: "old.zip", path: "old.zip" })], { scope: "shared" }) : listing(root),
    }));
    await screen.findByText("ubuntu.iso");
    await user.click(await screen.findByRole("radio", { name: "Shared" }));
    expect(await screen.findByRole("link", { name: "Save a copy of old.zip" })).toHaveAttribute(
      "href",
      "/api/files/download?path=old.zip&scope=shared",
    );
  });

  it("does not show a shared folder that is empty", async () => {
    renderApp("/files", local({ "GET /files": listing(root) }));
    await screen.findByText("ubuntu.iso");
    expect(screen.queryByRole("radiogroup", { name: "Storage area" })).not.toBeInTheDocument();
  });
});
