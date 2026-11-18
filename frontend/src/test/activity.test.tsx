import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { local, makeActivity, page, renderApp } from "./harness";

const entries = [
  makeActivity({ id: 7, message: "Downloaded ubuntu.iso" }),
  makeActivity({ id: 8, category: "admin", action: "settings.updated", message: "Settings changed: theme" }),
];

describe("activity", () => {
  it("deletes a single entry", async () => {
    const { user, server } = renderApp("/activity", local({
      "GET /activity": page(entries),
      "DELETE /activity/8": { deleted: 1 },
    }));
    await user.click(await screen.findByRole("button", { name: "Delete activity: Settings changed: theme" }));
    await waitFor(() => expect(server.find("DELETE", "/activity/8")).toHaveLength(1));
    expect(server.find("DELETE", "/activity/7")).toHaveLength(0);
  });

  it("clears all activity only after confirming", async () => {
    const { user, server } = renderApp("/activity", local({
      "GET /activity": page(entries),
      "DELETE /activity": { deleted: 2 },
    }));
    await user.click(await screen.findByRole("button", { name: "Clear activity" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(/All 2 entries will be deleted/)).toBeInTheDocument();
    expect(dialog.getByText(/Downloads and files are not touched/)).toBeInTheDocument();
    expect(server.find("DELETE", "/activity")).toHaveLength(0);
    await user.click(dialog.getByRole("button", { name: "Clear activity" }));
    await waitFor(() => expect(server.find("DELETE", "/activity")).toHaveLength(1));
    expect([...server.find("DELETE", "/activity")[0].query.keys()]).toEqual([]);
  });

  it("deletes only the entries matching the filters", async () => {
    const { user, server } = renderApp("/activity", local({
      "GET /activity": page(entries),
      "DELETE /activity": { deleted: 1 },
    }));
    await user.selectOptions(await screen.findByLabelText("Filter by category"), "admin");
    await user.click(await screen.findByRole("button", { name: "Delete matching" }));
    const dialog = within(await screen.findByRole("dialog"));
    await user.click(dialog.getByRole("button", { name: "Delete matching" }));
    await waitFor(() => expect(server.find("DELETE", "/activity")).toHaveLength(1));
    expect(server.find("DELETE", "/activity")[0].query.get("category")).toBe("admin");
  });

  it("does not offer delete buttons on the overview", async () => {
    renderApp("/", local({ "GET /activity": page(entries) }));
    expect(await screen.findByText("Downloaded ubuntu.iso")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete activity/ })).not.toBeInTheDocument();
  });
});
