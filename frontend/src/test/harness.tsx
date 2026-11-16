// A small in-memory stand-in for the FETCH API, so that the tests exercise the
// real components, hooks and request code against realistic responses.

import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { App } from "@/App";
import type { ActivityItem, AdminUser, Bootstrap, Download, FileItem, Me, Provider, QuotaLimits } from "@/lib/types";

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
  headers: Record<string, string>;
}

type Reply = unknown | ((call: Call) => unknown);
export type Routes = Record<string, Reply>;

export class HttpFailure {
  constructor(public status: number, public code: string, public message: string) {}
}

const NOW = "2026-01-10T12:00:00Z";

export const limits: QuotaLimits = {
  max_concurrent: 2,
  max_storage_bytes: 50 * 1024 ** 3,
  max_file_bytes: 0,
  daily_downloads: 0,
  bandwidth_bps: 0,
  max_queued: 100,
};

export const bootstrap: Bootstrap = {
  app_name: "FETCH",
  version: "1.0.0",
  setup_required: false,
  invitations_required: true,
  default_theme: "dark",
  language: "en",
  password_min_length: 10,
  password_require_mixed: false,
};

export function makeMe(role: "admin" | "user" = "user", overrides: Partial<Me["user"]> = {}): Me {
  return {
    user: {
      id: role === "admin" ? 1 : 2,
      username: role === "admin" ? "alice" : "bob",
      display_name: role === "admin" ? "Alice" : "Bob",
      role,
      is_active: true,
      theme: "",
      must_change_password: false,
      created_at: NOW,
      last_login_at: NOW,
      ...overrides,
    },
    csrf_token: "csrf-test-token",
    quota: { limits, usage: { storage_bytes: 1024 ** 3, active_downloads: 1, open_downloads: 2, downloads_today: 3 } },
    session_expires_at: "2026-01-17T12:00:00Z",
  };
}

export function makeDownload(overrides: Partial<Download> = {}): Download {
  return {
    id: 1,
    user_id: 2,
    owner: "bob",
    url: "https://files.example.com/ubuntu.iso",
    provider: "direct",
    filename: "ubuntu.iso",
    state: "downloading",
    priority: 1,
    position: 1,
    destination: "private",
    folder: "",
    total_bytes: 4 * 1024 ** 3,
    downloaded_bytes: 1024 ** 3,
    speed_bps: 5 * 1024 ** 2,
    avg_speed_bps: 4 * 1024 ** 2,
    eta_seconds: 600,
    engine: "native",
    attempts: 1,
    max_attempts: 4,
    error_code: "",
    error_message: "",
    wait_until: null,
    wait_reason: "",
    final_path: "",
    created_at: NOW,
    started_at: NOW,
    finished_at: null,
    ...overrides,
  };
}

export function makeFile(overrides: Partial<FileItem> = {}): FileItem {
  return {
    name: "ubuntu.iso",
    path: "ubuntu.iso",
    is_dir: false,
    size: 4 * 1024 ** 3,
    type: "archive",
    modified_at: NOW,
    downloaded_at: NOW,
    provider: "direct",
    source_url: "https://files.example.com/ubuntu.iso",
    owner: "",
    can_delete: true,
    ...overrides,
  };
}

export function makeProvider(overrides: Partial<Provider> = {}): Provider {
  return {
    id: "direct",
    name: "Direct HTTP",
    description: "Plain http and https links.",
    homepage: "",
    capabilities: {
      inspect: true, download: true, resume: true, anonymous: true, credentials: false,
      requires_credentials: false, folders: false, checksum: false, waits: false, captcha: false,
    },
    notes: ["Resume depends on the server supporting range requests."],
    verification: "Covered by automated tests with real transfers.",
    enabled: true,
    configured: false,
    ready: true,
    ...overrides,
  };
}

export function makeAdminUser(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    ...makeMe("user").user,
    quota: limits,
    storage_bytes: 1024 ** 3,
    open_downloads: 1,
    total_downloads: 7,
    active_sessions: 1,
    ...overrides,
  };
}

export function makeActivity(overrides: Partial<ActivityItem> = {}): ActivityItem {
  return {
    id: 1, created_at: NOW, category: "download", action: "download.completed", level: "info",
    message: "Downloaded ubuntu.iso", target: "1", username: "bob", ip_address: "", ...overrides,
  };
}

export const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 50 });

/** Answers shared by most tests for a signed-in session. */
export function signedIn(me: Me, extra: Routes = {}): Routes {
  return {
    "GET /bootstrap": bootstrap,
    "GET /auth/me": me,
    "GET /downloads": page<Download>([]),
    "GET /downloads/stats": { by_state: {}, active: 0, queued: 0, completed: 0, failed: 0, speed_bps: 0 },
    "GET /activity": page<ActivityItem>([]),
    "GET /files/usage": { private: { used_bytes: 1024 ** 3, limit_bytes: 50 * 1024 ** 3 }, shared: { enabled: true, used_bytes: 0 } },
    "GET /providers": [makeProvider()],
    "GET /users": [],
    "GET /settings": new HttpFailure(403, "forbidden", "Administrator access is required."),
    ...extra,
  };
}

export function mockApi(routes: Routes) {
  const calls: Call[] = [];
  const handler = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://fetch.test");
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.pathname.replace(/^\/api/, "");
    const call: Call = {
      method,
      path,
      query: url.searchParams,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    const key = `${method} ${path}`;
    if (!(key in routes)) {
      return new Response(JSON.stringify({ error: { code: "not_found", message: `No test route for ${key}` } }), { status: 404 });
    }
    let reply = routes[key];
    if (typeof reply === "function") reply = (reply as (call: Call) => unknown)(call);
    if (reply instanceof HttpFailure) {
      return new Response(JSON.stringify({ error: { code: reply.code, message: reply.message } }), { status: reply.status });
    }
    return new Response(JSON.stringify(reply), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", handler);
  return {
    calls,
    routes,
    find: (method: string, path: string) => calls.filter((call) => call.method === method && call.path === path),
  };
}

export function renderApp(path: string, routes: Routes) {
  window.history.replaceState(null, "", path);
  const server = mockApi(routes);
  const user = userEvent.setup();
  const view = render(<App />);
  return { ...view, server, user };
}
