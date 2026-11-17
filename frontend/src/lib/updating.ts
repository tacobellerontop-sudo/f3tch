// Remembers, for this browser tab, that an update was started from the
// dashboard, so the app can show a waiting screen while the server restarts.

import { useSyncExternalStore } from "react";

const KEY = "fetch-updating";
/** After this long the waiting screen gives up and says where to look. */
export const UPDATE_TIMEOUT_MS = 15 * 60_000;

export interface UpdatingState {
  fromVersion: string;
  startedAt: number;
}

const listeners = new Set<() => void>();
let cachedRaw: string | null = null;
let cachedValue: UpdatingState | null = null;

function read(): UpdatingState | null {
  let raw: string | null = null;
  try {
    raw = window.sessionStorage.getItem(KEY);
  } catch {
    raw = null;
  }
  if (raw === cachedRaw) return cachedValue;
  cachedRaw = raw;
  cachedValue = null;
  if (raw) {
    try {
      const value = JSON.parse(raw) as UpdatingState;
      if (typeof value.fromVersion === "string" && typeof value.startedAt === "number") cachedValue = value;
    } catch {
      cachedValue = null;
    }
  }
  return cachedValue;
}

function write(value: UpdatingState | null): void {
  try {
    if (value) window.sessionStorage.setItem(KEY, JSON.stringify(value));
    else window.sessionStorage.removeItem(KEY);
  } catch {
    // Without session storage the waiting screen simply does not survive a reload.
    cachedRaw = value ? JSON.stringify(value) : null;
    cachedValue = value;
  }
  listeners.forEach((listener) => listener());
}

export function markUpdating(fromVersion: string): void {
  write({ fromVersion, startedAt: Date.now() });
}

export function clearUpdating(): void {
  write(null);
}

export function useUpdating(): UpdatingState | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    read,
    () => null,
  );
}
