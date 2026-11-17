// A small in-memory stand-in for the FETCH API, so that the tests exercise the
// real components, hooks and request code against realistic responses.

import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { App } from "@/App";
import type { ActivityItem, Bootstrap, Download, FileItem, Provider } from "@/lib/types";

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

export const bootstrap: Bootstrap = {
  app_name: "FETCH",
  version: "1.0.0",
  default_theme: "dark",
  language: "en",
  theme: "",
  usage: {
    storage_bytes: 1024 ** 3,
    storage_limit_bytes: 50 * 1024 ** 3,
    max_concurrent: 3,
    active_downloads: 1,
    open_downloads: 2,
    downloads_today: 3,
  },
};

export function makeDownload(overrides: Partial<Download> = {}): Download {
  return {
    id: 1,
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

export function makeActivity(overrides: Partial<ActivityItem> = {}): ActivityItem {
  return {
    id: 1, created_at: NOW, category: "download", action: "download.completed", level: "info",
    message: "Downloaded ubuntu.iso", target: "1", ...overrides,
  };
}

export const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 50 });

/** Answers shared by most tests: FETCH running, nothing special going on. */
export function local(extra: Routes = {}): Routes {
  return {
    "GET /bootstrap": bootstrap,
    "GET /downloads": page<Download>([]),
    "GET /downloads/stats": { by_state: {}, active: 0, queued: 0, completed: 0, failed: 0, speed_bps: 0 },
    "GET /activity": page<ActivityItem>([]),
    "GET /files/usage": {
      private: { used_bytes: 1024 ** 3, limit_bytes: 50 * 1024 ** 3 },
      shared: { used_bytes: 0 },
      disk: { total: 500 * 1024 ** 3, used: 200 * 1024 ** 3, free: 300 * 1024 ** 3 },
    },
    "GET /providers": [makeProvider()],
    "GET /settings": new HttpFailure(404, "not_found", "No test settings."),
    "GET /admin/update": new HttpFailure(404, "not_found", "No test update status."),
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
