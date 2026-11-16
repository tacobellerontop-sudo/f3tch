import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { type Call, makeAdminUser, makeFile, makeMe, renderApp, signedIn } from "./harness";

const listing = (items: ReturnType<typeof makeFile>[], extra: Record<string, unknown> = {}) => ({
  scope: "private", path: "", owner_id: 2, items, total: items.length, page: 1, page_size: 1000, can_write: true, ...extra,
});

const root = [
  makeFile({ name: "movies", path: "movies", is_dir: true, size: 0, type: "folder", provider: "", downloaded_at: null }),
  makeFile(),
];

describe("file browser", () => {
  it("lists files with a same-origin download link and opens folders", async () => {
    const { user, server } = renderApp("/files", signedIn(makeMe("user"), {
      "GET /files": (call: Call) =>
        call.query.get("path") === "movies"
          ? listing([makeFile({ name: "film.mkv", path: "movies/film.mkv", type: "video" })], { path: "movies" })
          : listing(root),
    }));

    const link = await screen.findByRole("link", { name: "Download ubuntu.iso" });
    expect(link).toHaveAttribute("href", "/api/files/download?path=ubuntu.iso&scope=private");
    expect(screen.getAllByText("4.0 GB").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "movies" }));
    expect(await screen.findByText("film.mkv")).toBeInTheDocument();
    expect(window.location.search).toContain("path=movies");
    expect(screen.getByRole("link", { name: "Download film.mkv" })).toHaveAttribute(
      "href",
      "/api/files/download?path=movies%2Ffilm.mkv&scope=private",
    );
    const crumbs = within(screen.getByRole("navigation", { name: "Folder path" }));
    await user.click(crumbs.getByRole("button", { name: "My files" }));
    expect(await screen.findByText("ubuntu.iso")).toBeInTheDocument();
    expect(server.find("GET", "/files").some((call) => call.query.get("path") === "movies")).toBe(true);
  });

  it("deletes a file only after confirmation", async () => {
    const { user, server } = renderApp("/files", signedIn(makeMe("user"), {
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
    expect(call.headers["X-CSRF-Token"]).toBe("csrf-test-token");
  });

  it("creates a folder in the current location", async () => {
    const { user, server } = renderApp("/files?path=movies", signedIn(makeMe("user"), {
      "GET /files": listing([], { path: "movies" }),
      "POST /files/folder": { ok: true, name: "2026" },
    }));
    await user.click(await screen.findByRole("button", { name: "New folder" }));
    await user.type(screen.getByLabelText("Folder name"), "2026");
    await user.click(screen.getByRole("button", { name: "Create folder" }));
    await waitFor(() => expect(server.find("POST", "/files/folder")).toHaveLength(1));
    expect(server.find("POST", "/files/folder")[0].body).toEqual({ scope: "private", path: "movies", name: "2026", owner_id: null });
  });

  it("offers no write or delete controls in a read-only shared folder", async () => {
    renderApp("/files?scope=shared", signedIn(makeMe("user"), {
      "GET /files": listing([makeFile({ can_delete: false, owner: "alice" })], { scope: "shared", owner_id: null, can_write: false }),
    }));
    expect(await screen.findByRole("link", { name: "Download ubuntu.iso" })).toHaveAttribute(
      "href",
      "/api/files/download?path=ubuntu.iso&scope=shared",
    );
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete ubuntu.iso" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New folder" })).not.toBeInTheDocument();
  });

  it("never sends another user's id for a regular user, even when the URL asks for it", async () => {
    const { server } = renderApp("/files?owner=1", signedIn(makeMe("user"), { "GET /files": listing(root) }));
    await screen.findByText("ubuntu.iso");
    expect(server.find("GET", "/files").every((call) => !call.query.has("owner_id"))).toBe(true);
    expect(screen.queryByLabelText("Whose files")).not.toBeInTheDocument();
  });

  it("lets an administrator browse another user's files", async () => {
    const { server } = renderApp("/files?owner=2", signedIn(makeMe("admin"), {
      "GET /users": [makeAdminUser({ id: 1, username: "alice", role: "admin" }), makeAdminUser({ id: 2, username: "bob" })],
      "GET /files": listing(root),
    }));
    await screen.findByText("ubuntu.iso");
    expect(server.find("GET", "/files")[0].query.get("owner_id")).toBe("2");
    expect(await screen.findByRole("button", { name: "bob's files" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download ubuntu.iso" })).toHaveAttribute(
      "href",
      "/api/files/download?path=ubuntu.iso&scope=private&owner_id=2",
    );
  });
});
