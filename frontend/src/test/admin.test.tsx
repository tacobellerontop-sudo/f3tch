import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AppSettings, SettingsPayload, UpdateStatus } from "@/lib/types";

import { type Call, HttpFailure, makeAdminUser, makeMe, makeProvider, renderApp, signedIn } from "./harness";

const settings: AppSettings = {
  general: { app_name: "FETCH", theme: "dark", language: "en", default_destination: "private", check_for_updates: true },
  downloads: {
    max_global_concurrent: 3, max_per_user_concurrent: 2, retry_count: 3, retry_delay_seconds: 30, default_priority: 1,
    use_aria2: true, aria2_path: "", aria2_min_size_mb: 16, aria2_connections: 4,
  },
  storage: {
    storage_root: "", max_storage_gb: 0, temp_cleanup_hours: 72, auto_delete_days: 0, history_retention_days: 0,
    shared_enabled: true, users_can_write_shared: true,
  },
  network: { bind_address: "", port: 0, trusted_proxies: [], cors_origins: [], public_url: "" },
  security: {
    session_hours: 168, invitations_required: true, invitation_expiry_hours: 72, password_min_length: 10,
    password_require_mixed: false, login_max_attempts: 5, login_window_minutes: 15, login_lockout_minutes: 15,
  },
  quota_defaults: { max_concurrent: 2, max_storage_gb: 50, max_file_gb: 0, daily_downloads: 0, bandwidth_kbps: 0, max_queued: 100 },
};

const payload: SettingsPayload = {
  settings,
  effective: {
    bind_address: "127.0.0.1", port: 8787, storage_root: "C:\\FETCH\\storage", active_storage_root: "C:\\FETCH\\storage",
    config_file: "C:\\FETCH\\config\\fetch.toml",
  },
  restart_required: [],
};

const alice = makeAdminUser({ id: 1, username: "alice", display_name: "Alice", role: "admin" });
const bob = makeAdminUser({ id: 2, username: "bob", display_name: "Bob" });

function admin(extra = {}) {
  return signedIn(makeMe("admin"), {
    "GET /users": [alice, bob],
    "GET /invitations": [],
    "GET /admin/sessions": [],
    "GET /settings": payload,
    "GET /auth/sessions": [],
    ...extra,
  });
}

describe("access control in the interface", () => {
  it("keeps regular users out of the Users page and the server settings", async () => {
    const { server } = renderApp("/users", signedIn(makeMe("user"), { "GET /auth/sessions": [] }));
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
    expect(server.find("GET", "/invitations")).toHaveLength(0);
  });

  it("shows a regular user only their account settings", async () => {
    const { server } = renderApp("/settings/security", signedIn(makeMe("user"), { "GET /auth/sessions": [] }));
    expect(await screen.findByText("Signed-in devices")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/settings");
    expect(screen.queryByRole("navigation", { name: "Settings sections" })).not.toBeInTheDocument();
    expect(server.find("GET", "/settings")).toHaveLength(0);
  });

  it("does not offer provider administration to regular users", async () => {
    renderApp("/providers", signedIn(makeMe("user"), {
      "GET /providers": [makeProvider({ id: "rapidgator", name: "Rapidgator", ready: false, capabilities: { ...makeProvider().capabilities, anonymous: false, credentials: true, requires_credentials: true, captcha: true } })],
    }));
    expect(await screen.findByText("Rapidgator")).toBeInTheDocument();
    expect(screen.getByText("Needs credentials")).toBeInTheDocument();
    expect(screen.getByText("CAPTCHA links are not downloaded")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /credentials/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});

describe("user administration", () => {
  it("creates an invitation and shows the link once", async () => {
    const { user, server } = renderApp("/users", admin({
      "POST /invitations": {
        id: 9, role: "user", note: "For Carol", created_at: "2026-01-10T12:00:00Z", expires_at: "2026-01-13T12:00:00Z",
        used_at: null, revoked: false, status: "active", token: "tok123", path: "/invite/tok123", url: "https://fetch.tailnet.ts.net/invite/tok123",
      },
    }));
    await user.click(await screen.findByRole("button", { name: "Invite user" }));
    const dialog = within(await screen.findByRole("dialog"));
    await user.type(dialog.getByLabelText("Note (optional)"), "For Carol");
    await user.click(dialog.getByRole("button", { name: "Create link" }));
    expect(await dialog.findByDisplayValue("https://fetch.tailnet.ts.net/invite/tok123")).toBeInTheDocument();
    expect(server.find("POST", "/invitations")[0].body).toEqual({ role: "user", note: "For Carol", expires_hours: null });
  });

  it("saves a quota in bytes from the values entered in GB and KB/s", async () => {
    const { user, server } = renderApp("/users", admin({ "PUT /users/2/quota": (call: Call) => call.body }));
    await user.click(await screen.findByRole("button", { name: "Actions for bob" }));
    await user.click(screen.getByRole("menuitem", { name: "Edit quota" }));
    const dialog = within(await screen.findByRole("dialog"));
    const storage = dialog.getByLabelText("Storage (GB)");
    expect(storage).toHaveValue(50);
    await user.clear(storage);
    await user.type(storage, "10");
    await user.clear(dialog.getByLabelText("Speed limit (KB/s)"));
    await user.type(dialog.getByLabelText("Speed limit (KB/s)"), "500");
    await user.click(dialog.getByRole("button", { name: "Save quota" }));
    await waitFor(() => expect(server.find("PUT", "/users/2/quota")).toHaveLength(1));
    expect(server.find("PUT", "/users/2/quota")[0].body).toEqual({
      max_concurrent: 2, max_storage_bytes: 10 * 1024 ** 3, max_file_bytes: 0, daily_downloads: 0, bandwidth_bps: 512_000, max_queued: 100,
    });
  });

  it("disables an account, and never offers that for the administrator's own account", async () => {
    const { user, server } = renderApp("/users", admin({ "PATCH /users/2": { ...bob, is_active: false } }));
    await user.click(await screen.findByRole("button", { name: "Actions for alice" }));
    expect(screen.getByRole("menuitem", { name: "Disable account" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Delete user" })).toBeDisabled();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Actions for bob" }));
    await user.click(screen.getByRole("menuitem", { name: "Disable account" }));
    await waitFor(() => expect(server.find("PATCH", "/users/2")).toHaveLength(1));
    expect(server.find("PATCH", "/users/2")[0].body).toEqual({ is_active: false });
  });

  it("deletes a user only after confirmation and keeps their files unless asked", async () => {
    const { user, server } = renderApp("/users", admin({ "DELETE /users/2": { ok: true } }));
    await user.click(await screen.findByRole("button", { name: "Actions for bob" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete user" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(server.find("DELETE", "/users/2")).toHaveLength(0);
    await user.click(dialog.getByRole("button", { name: "Delete user" }));
    await waitFor(() => expect(server.find("DELETE", "/users/2")).toHaveLength(1));
    expect(server.find("DELETE", "/users/2")[0].query.get("delete_files")).toBe("false");
  });

  it("shows a generated temporary password exactly once after a reset", async () => {
    const { user } = renderApp("/users", admin({ "POST /users/2/reset-password": { ok: true, temporary_password: "Temp-Pass-1234567" } }));
    await user.click(await screen.findByRole("button", { name: "Actions for bob" }));
    await user.click(screen.getByRole("menuitem", { name: "Reset password" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reset password" }));
    expect(await screen.findByDisplayValue("Temp-Pass-1234567")).toBeInTheDocument();
  });
});

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
    const limit = await screen.findByLabelText("Simultaneous downloads (server)");
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
    const { user } = renderApp("/settings/network", admin({
      "PUT /settings": new HttpFailure(422, "invalid_settings", "network.bind_address: Bind address must be an IP address, e.g. 127.0.0.1."),
    }));
    await user.type(await screen.findByLabelText("Bind address"), "localhost");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bind address must be an IP address");
  });

  it("changes the password and requires the repeat to match", async () => {
    const { user, server } = renderApp("/settings", admin({ "POST /auth/password": { ok: true, sessions_revoked: 2 } }));
    await user.type(await screen.findByLabelText("Current password"), "old password here");
    await user.type(screen.getByLabelText("New password"), "new password here");
    await user.type(screen.getByLabelText("Repeat new password"), "different");
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
    await user.clear(screen.getByLabelText("Repeat new password"));
    await user.type(screen.getByLabelText("Repeat new password"), "new password here");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(server.find("POST", "/auth/password")).toHaveLength(1));
    expect(server.find("POST", "/auth/password")[0].body).toEqual({ current_password: "old password here", new_password: "new password here" });
    expect(await screen.findByText(/2 other sessions were signed out/)).toBeInTheDocument();
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

  it("does not ask for update status on behalf of regular users", async () => {
    const { server } = renderApp("/settings/updates", signedIn(makeMe("user"), { "GET /auth/sessions": [] }));
    expect(await screen.findByText("Signed-in devices")).toBeInTheDocument();
    expect(server.find("GET", "/admin/update")).toHaveLength(0);
  });
});
