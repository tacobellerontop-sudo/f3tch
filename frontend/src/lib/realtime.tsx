// WebSocket event stream: keeps the query cache and the progress store fresh.
//
// While the socket is connected nothing polls. If it drops, the client
// reconnects with exponential backoff and, meanwhile, queries fall back to a
// slow refetch interval (see `useLiveInterval`).

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { progressStore } from "./progress";
import type { Download, Page, Progress, ServerEvent } from "./types";

export type ConnectionStatus = "connecting" | "online" | "reconnecting" | "offline";

const RealtimeContext = createContext<ConnectionStatus>("offline");

export function useConnectionStatus(): ConnectionStatus {
  return useContext(RealtimeContext);
}

/** Refetch interval for queries: none while live, slow polling otherwise. */
export function useLiveInterval(fallbackMs = 5000): number | false {
  return useConnectionStatus() === "online" ? false : fallbackMs;
}

const TRANSFER_STATES = new Set(["downloading"]);

function patchLists(client: QueryClient, download: Download): boolean {
  let found = false;
  client.setQueriesData<Page<Download>>({ queryKey: ["downloads", "list"] }, (page) => {
    if (!page?.items) return page;
    const index = page.items.findIndex((item) => item.id === download.id);
    if (index === -1) return page;
    found = true;
    const items = page.items.slice();
    items[index] = download;
    return { ...page, items };
  });
  client.setQueryData(["downloads", "detail", download.id], download);
  return found;
}

function removeFromLists(client: QueryClient, id: number): void {
  client.setQueriesData<Page<Download>>({ queryKey: ["downloads", "list"] }, (page) => {
    if (!page?.items?.some((item) => item.id === id)) return page;
    return { ...page, items: page.items.filter((item) => item.id !== id), total: Math.max(0, page.total - 1) };
  });
  client.removeQueries({ queryKey: ["downloads", "detail", id] });
}

/** Apply one server event to client state. Exported for tests. */
export function applyServerEvent(client: QueryClient, event: ServerEvent, invalidate: (key: string) => void): void {
  const { type, data } = event;
  if (type === "download.progress") {
    progressStore.set(data as unknown as Progress);
    return;
  }
  if (type === "download.removed") {
    const id = data.id as number;
    progressStore.clear(id);
    removeFromLists(client, id);
    invalidate("downloads");
    return;
  }
  if (type.startsWith("download.")) {
    const download = data.download as Download | undefined;
    if (!download) return;
    if (!TRANSFER_STATES.has(download.state)) progressStore.clear(download.id);
    const known = patchLists(client, download);
    // A job this list has not seen (new, or moved into its filter): refetch.
    if (!known || type !== "download.updated") invalidate("downloads");
    if (download.state === "completed") {
      invalidate("files");
      invalidate("bootstrap");
    }
    return;
  }
  switch (type) {
    case "queue.changed":
      invalidate("downloads");
      break;
    case "storage.changed":
      invalidate("files");
      invalidate("bootstrap");
      break;
    case "activity":
    case "activity.deleted":
      invalidate("activity");
      break;
    case "settings.changed":
      invalidate("bootstrap");
      invalidate("settings");
      break;
    case "providers.changed":
      invalidate("providers");
      break;
    default:
      break;
  }
}

interface Props {
  enabled: boolean;
  children: ReactNode;
}

export function RealtimeProvider({ enabled, children }: Props) {
  const client = useQueryClient();
  const [status, setStatus] = useState<ConnectionStatus>(enabled ? "connecting" : "offline");

  useEffect(() => {
    if (!enabled) {
      setStatus("offline");
      progressStore.reset();
      return;
    }
    let socket: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let flushTimer: ReturnType<typeof setTimeout> | undefined;
    const pending = new Set<string>();

    // Bursts of events (a batch submission, a bulk action) collapse into one
    // refetch per query family.
    const invalidate = (key: string) => {
      pending.add(key);
      if (flushTimer) return;
      flushTimer = setTimeout(() => {
        flushTimer = undefined;
        const keys = [...pending];
        pending.clear();
        keys.forEach((name) => void client.invalidateQueries({ queryKey: [name] }));
      }, 250);
    };

    const connect = () => {
      if (closed) return;
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${window.location.host}/api/ws`);
      socket.onopen = () => {
        const wasReconnect = attempt > 0;
        attempt = 0;
        setStatus("online");
        // Anything may have changed while we were away.
        if (wasReconnect) void client.invalidateQueries();
      };
      socket.onmessage = (message) => {
        let event: ServerEvent;
        try {
          event = JSON.parse(message.data as string) as ServerEvent;
        } catch {
          return;
        }
        if (event.type === "ping" || event.type === "hello") return;
        applyServerEvent(client, event, invalidate);
      };
      socket.onclose = () => {
        socket = null;
        if (closed) return;
        progressStore.reset();
        attempt += 1;
        setStatus(attempt > 3 ? "offline" : "reconnecting");
        const delay = Math.min(30_000, 500 * 2 ** Math.min(attempt, 6)) + Math.random() * 400;
        retryTimer = setTimeout(connect, delay);
      };
      socket.onerror = () => socket?.close();
    };

    const onVisible = () => {
      if (document.visibilityState === "visible" && !socket && !closed) {
        clearTimeout(retryTimer);
        connect();
      }
    };
    setStatus("connecting");
    connect();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onVisible);

    return () => {
      closed = true;
      clearTimeout(retryTimer);
      clearTimeout(flushTimer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onVisible);
      socket?.close();
    };
  }, [client, enabled]);

  const value = useMemo(() => status, [status]);
  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>;
}
