import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AppSettings, SettingsPayload, UpdateStatus } from "@/lib/types";

import { type Call, HttpFailure, local, makeAuth, makeProvider, renderApp } from "./harness";

const settings: AppSettings = {
  general: {
    app_name: "FETCH", theme: "dark", language: "en", default_destination: "private", check_for_updates: true, close_to_tray: true, watch_clipboard: true,
  },
  downloads: {
    max_global_concurrent: 3, speed_limit_kbps: 0, retry_count: 3, retry_delay_seconds: 30, default_priority: 1,
    use_aria2: true, aria2_path: "", aria2_min_size_mb: 16, aria2_connections: 8,
    connections_per_download: 8, media_quality: "best",
  },
  storage: { storage_root: "", max_storage_gb: 0, temp_cleanup_hours: 72, auto_delete_days: 0, history_retention_days: 0 },
  network: { port: 0 },
};

const payload: SettingsPayload = {
  settings,
  effective: {
    bind_address: "127.0.0.1", port: 8787, storage_root: "C:\\FETCH\\storage", active_storage_root: "C:\\FETCH\\storage",
    config_file: "C:\\FETCH\\config\\fetch.toml",
  },
  restart_required: [],
};

function admin(extra = {}) {
  return local({ "GET /settings": payload, ...extra });
}

describe("providers and settings", () => {
  const rapidgator = makeProvider({
    id: "rapidgator", name: "Rapidgator", ready: false, health: "unknown", health_message: "", checked_at: null,
    credential_fields_set: [], credentials_updated_at: null,
    credential_fields: [
      { name: "login", label: "Email", kind: "text", required: true, help: "" },
      { name: "password", label: "Password", kind: "password", required: true, help: "" },
    ],
  });

  it("stores provider credentials and never renders stored values", async () => {
    const { user, server } = renderApp("/providers", admin({
      "GET /providers": [rapidgator],
      "PUT /providers/rapidgator/credentials": { ...rapidgator, configured: true, ready: true },
    }));
    await user.click(await screen.findByRole("button", { name: "Add credentials" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(dialog.getByLabelText("Password")).toHaveValue("");
    await user.type(dialog.getByLabelText("Email"), "me@example.com");
    await user.type(dialog.getByLabelText("Password"), "s3cret");
    await user.click(dialog.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(server.find("PUT", "/providers/rapidgator/credentials")).toHaveLength(1));
    expect(server.find("PUT", "/providers/rapidgator/credentials")[0].body).toEqual({ values: { login: "me@example.com", password: "s3cret" } });
  });

  it("shows the sign-in state of a provider and lets the mode be chosen", async () => {
    const configured = { ...rapidgator, ready: true, configured: true, auth: makeAuth({ modes: ["anonymous", "account"], account_configured: true, account_valid: true, effective_mode: "account", status: "account" }) };
    const { user, server } = renderApp("/providers", admin({
      "GET /providers": [configured, makeProvider({ id: "mega", name: "MEGA", enabled: true, auth: makeAuth({ provider_available: false, status: "unavailable", message: "MEGA was unreachable the last time FETCH contacted it." }) })],
      "PUT /providers/rapidgator/auth-mode": { ...configured, auth: makeAuth({ modes: ["anonymous", "account"], account_configured: true, preference: "anonymous" }) },
    }));
    expect(await screen.findByText("Using stored account")).toBeInTheDocument();
    expect(screen.getAllByText("Anonymous access available").length).toBeGreaterThan(0);
    expect(screen.getByText("Account configured and verified")).toBeInTheDocument();
    expect(screen.getByText("Provider unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Sign-in mode for MEGA" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Sign-in mode for Rapidgator" }), "anonymous");
    await waitFor(() => expect(server.find("PUT", "/providers/rapidgator/auth-mode")).toHaveLength(1));
    expect(server.find("PUT", "/providers/rapidgator/auth-mode")[0].body).toEqual({ mode: "anonymous" });
  });

  it("runs a connection test and reports a failure honestly", async () => {
    const { user } = renderApp("/providers", admin({
      "GET /providers": [rapidgator],
      "POST /providers/rapidgator/test": { ok: false, message: "No credentials are configured for Rapidgator.", account: {} },
    }));
    await user.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("No credentials are configured for Rapidgator.")).toBeInTheDocument();
  });

  it("saves only the edited settings section and announces a required restart", async () => {
    let current = payload;
    const { user, server } = renderApp("/settings/downloads", admin({
      "GET /settings": () => current,
      "PUT /settings": (call: Call) => {
        current = { ...payload, settings: { ...settings, ...(call.body as Partial<AppSettings>) }, restart_required: ["downloads.use_aria2"] };
        return { ...current, changed: ["downloads.max_global_concurrent", "downloads.use_aria2"] };
      },
    }));
    const limit = await screen.findByLabelText("Simultaneous downloads");
    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    await user.clear(limit);
    await user.type(limit, "5");
    await user.click(screen.getByRole("switch", { name: /Use aria2 for large files/ }));
    await user.click(save);
    await waitFor(() => expect(server.find("PUT", "/settings")).toHaveLength(1));
    expect(server.find("PUT", "/settings")[0].body).toEqual({
      downloads: { ...settings.downloads, max_global_concurrent: 5, use_aria2: false },
    });
    expect(await screen.findByText(/Restart FETCH to apply: aria2 engine/)).toBeInTheDocument();
  });

  it("shows the validation message from the server", async () => {
    const { user } = renderApp("/settings/advanced", admin({
      "PUT /settings": new HttpFailure(422, "invalid_settings", "network.port: Port must be 0 (default) or between 1024 and 65535."),
    }));
    expect(await screen.findByText(/not reachable from other devices/)).toBeInTheDocument();
    const port = screen.getByLabelText("Port");
    await user.clear(port);
    await user.type(port, "80");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Port must be 0 (default) or between 1024 and 65535");
  });

  it("offers no account, password, network exposure or quota settings", async () => {
    renderApp("/settings", admin());
    const sections = within(await screen.findByRole("navigation", { name: "Settings sections" }));
    expect(sections.getAllByRole("link").map((link) => link.textContent)).toEqual([
      "General", "Downloads", "Storage", "Advanced", "Updates", "Diagnostics", "Logs",
    ]);
    expect(await screen.findByRole("switch", { name: /Keep running when the window is closed/ })).toBeChecked();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Bind address")).not.toBeInTheDocument();
  });

  it("saves the theme for next time", async () => {
    const { user, server } = renderApp("/settings", admin({ "PATCH /preferences": { theme: "light" } }));
    await user.click(await screen.findByRole("radio", { name: "Light" }));
    await waitFor(() => expect(server.find("PATCH", "/preferences")).toHaveLength(1));
    expect(server.find("PATCH", "/preferences")[0].body).toEqual({ theme: "light" });
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("closes to the tray unless that is switched off", async () => {
    const { user, server } = renderApp("/settings", admin({ "PUT /settings": (call: Call) => ({ ...payload, changed: ["general.close_to_tray"], ...{ settings: { ...settings, ...(call.body as object) } } }) }));
    await user.click(await screen.findByRole("switch", { name: /Keep running when the window is closed/ }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(server.find("PUT", "/settings")).toHaveLength(1));
    expect(server.find("PUT", "/settings")[0].body).toEqual({ general: { ...settings.general, close_to_tray: false } });
  });
});

const upToDate: UpdateStatus = {
  current_version: "1.0.0", repository: "example/fetch", auto_check: true, checked_at: "2026-01-02T03:04:05Z", error: "",
  latest_version: "1.0.0", update_available: false, release_name: "FETCH 1.0.0", notes: "", published_at: "2026-01-01T00:00:00Z",
  release_url: "https://github.com/example/fetch/releases/tag/v1.0.0", package_available: true, can_apply: true, apply_blocker: "",
  source: "release", in_progress: false, last_run: null,
};
const newer: UpdateStatus = {
  ...upToDate, latest_version: "1.1.0", update_available: true, release_name: "FETCH 1.1.0", notes: "Faster queue handling.",
  release_url: "https://github.com/example/fetch/releases/tag/v1.1.0",
};

describe("updates", () => {
  it("shows the installed version and checks again on request", async () => {
    let answer = upToDate;
    const { user, server } = renderApp("/settings/updates", admin({ "GET /admin/update": () => answer }));
    expect(await screen.findByRole("heading", { name: "FETCH 1.0.0" })).toBeInTheDocument();
    expect(screen.getByText("Up to date")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install update" })).not.toBeInTheDocument();

    answer = newer;
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByText("Version 1.1.0 is available")).toBeInTheDocument();
    expect(screen.getByText("Faster queue handling.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View on GitHub/ })).toHaveAttribute("href", newer.release_url);
    const refreshed = server.find("GET", "/admin/update").filter((call) => call.query.get("refresh") === "true");
    expect(refreshed).toHaveLength(1);
  });

  it("installs an update only after confirmation, then waits for the server", async () => {
    const { user, server } = renderApp("/settings/updates", admin({
      "GET /admin/update": newer,
      "POST /admin/update/apply": { ok: true },
      "GET /health": { status: "ok", version: "1.0.0" },
    }));
    await user.click(await screen.findByRole("button", { name: "Install update" }));
    expect(server.find("POST", "/admin/update/apply")).toHaveLength(0);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Install FETCH 1.1.0/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Install update" }));
    expect(await screen.findByRole("heading", { name: "Updating FETCH" })).toBeInTheDocument();
    expect(server.find("POST", "/admin/update/apply")).toHaveLength(1);
    window.sessionStorage.clear();
  });

  it("explains why an update cannot be installed from the dashboard", async () => {
    renderApp("/settings/updates", admin({
      "GET /admin/update": { ...newer, can_apply: false, apply_blocker: "Updates can only be installed from the dashboard on Windows." },
    }));
    expect(await screen.findByText(/can only be installed from the dashboard on Windows/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install update" })).toBeDisabled();
  });

  it("offers the single-file executable its own update and manual instructions", async () => {
    renderApp("/settings/updates", admin({
      "GET /admin/update": { ...newer, source: "exe", can_apply: false, apply_blocker: "FETCH cannot write to its own folder." },
    }));
    expect(await screen.findByText(/replace FETCH.exe with the one from the release page/)).toBeInTheDocument();
    expect(screen.queryByText(/update\.ps1/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install update" })).toBeDisabled();
  });

  it("reports a failed check and a failed run without hiding the version", async () => {
    renderApp("/settings/updates", admin({
      "GET /admin/update": {
        ...upToDate, latest_version: null, error: "GitHub could not be reached.",
        last_run: { state: "failed", message: "Checksum mismatch.", from_version: "0.9.0", to_version: "1.0.0", started_at: "2026-01-01T00:00:00Z", finished_at: "2026-01-01T00:01:00Z" },
      },
    }));
    expect(await screen.findByText("GitHub could not be reached.")).toBeInTheDocument();
    expect(screen.getByText("The last update failed")).toBeInTheDocument();
    expect(screen.getByText("Checksum mismatch.")).toBeInTheDocument();
  });

  it("turns automatic checks off through the settings API", async () => {
    const { user, server } = renderApp("/settings/updates", admin({
      "GET /admin/update": upToDate,
      "PUT /settings": { ...payload, settings: { ...settings, general: { ...settings.general, check_for_updates: false } } },
    }));
    const toggle = await screen.findByRole("switch", { name: /Check for updates automatically/ });
    await waitFor(() => expect(toggle).toBeChecked());
    await user.click(toggle);
    await waitFor(() => expect(server.find("PUT", "/settings")).toHaveLength(1));
    expect(server.find("PUT", "/settings")[0].body).toEqual({ general: { check_for_updates: false } });
  });
});
