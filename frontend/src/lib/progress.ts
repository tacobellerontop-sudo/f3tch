// Live transfer progress lives outside React Query's cache.
//
// Progress events arrive up to twice a second per active download. Writing
// them into the query cache would re-render every list on every tick; instead
// each row subscribes to exactly its own download id.

import { useSyncExternalStore } from "react";

import type { Progress } from "./types";

type Listener = () => void;

const entries = new Map<number, Progress>();
const listeners = new Map<number, Set<Listener>>();
const globalListeners = new Set<Listener>();

function emit(id: number): void {
  listeners.get(id)?.forEach((listener) => listener());
  globalListeners.forEach((listener) => listener());
}

export const progressStore = {
  set(progress: Progress): void {
    entries.set(progress.id, progress);
    emit(progress.id);
  },
  clear(id: number): void {
    if (entries.delete(id)) emit(id);
  },
  reset(): void {
    const ids = [...entries.keys()];
    entries.clear();
    ids.forEach(emit);
  },
  get(id: number): Progress | undefined {
    return entries.get(id);
  },
  subscribe(id: number, listener: Listener): () => void {
    let set = listeners.get(id);
    if (!set) {
      set = new Set();
      listeners.set(id, set);
    }
    set.add(listener);
    return () => {
      set.delete(listener);
      if (set.size === 0) listeners.delete(id);
    };
  },
  subscribeAll(listener: Listener): () => void {
    globalListeners.add(listener);
    return () => globalListeners.delete(listener);
  },
};

/** Live progress of one download (undefined when it is not transferring). */
export function useProgress(id: number): Progress | undefined {
  return useSyncExternalStore(
    (listener) => progressStore.subscribe(id, listener),
    () => progressStore.get(id),
  );
}

/** Combined live speed of the given downloads (falls back to ``fallback`` values). */
export function useSpeedSum(downloads: { id: number; speed_bps: number }[]): number {
  return useSyncExternalStore(progressStore.subscribeAll, () => {
    let sum = 0;
    for (const item of downloads) sum += entries.get(item.id)?.speed_bps ?? item.speed_bps;
    return sum;
  });
}
