// Live speeds of every network connection, fed by the `network.speeds` event.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

import { api } from "./api";

export type AdapterKind = "wifi" | "ethernet" | "cellular" | "other";

export interface NetworkAdapter {
  id: string;
  name: string;
  address: string;
  kind: AdapterKind;
  link_mbps: number;
  online: boolean | null;
  latency_ms: number | null;
  selected: boolean;
  active: boolean;
  bps: number;
  total_bytes: number;
  history: number[];
}

export interface NetworkSnapshot {
  enabled: boolean;
  multi_path_active: boolean;
  interval_seconds: number;
  default: { bps: number; total_bytes: number; history: number[] };
  adapters: NetworkAdapter[];
  total_bps: number;
}

export interface SpeedSample {
  ts: number;
  default_bps: number;
  adapters: Record<string, number>;
  total_bps: number;
}

/** Seconds of history drawn by the chart. */
export const WINDOW = 60;
export const DEFAULT_PATH = "default";

type Series = Map<string, number[]>;

let series: Series = new Map();
let version = 0;
const listeners = new Set<() => void>();

function emit() {
  version += 1;
  listeners.forEach((listener) => listener());
}

function pushValue(next: Series, key: string, value: number) {
  const values = (next.get(key) ?? new Array<number>(WINDOW).fill(0)).slice(-(WINDOW - 1));
  values.push(Math.max(0, value));
  next.set(key, values);
}

export const speedStore = {
  /** Seed from the snapshot's history (on first load or after a reconnect). */
  seed(snapshot: NetworkSnapshot) {
    const next: Series = new Map();
    const fill = (history: number[]) => {
      const tail = history.slice(-WINDOW);
      return [...new Array<number>(WINDOW - tail.length).fill(0), ...tail];
    };
    next.set(DEFAULT_PATH, fill(snapshot.default.history));
    snapshot.adapters.forEach((adapter) => next.set(adapter.id, fill(adapter.history)));
    series = next;
    emit();
  },
  push(sample: SpeedSample) {
    const next: Series = new Map(series);
    pushValue(next, DEFAULT_PATH, sample.default_bps);
    Object.entries(sample.adapters).forEach(([key, value]) => pushValue(next, key, value));
    // A path that sent no sample this second (it vanished) still scrolls.
    next.forEach((_values, key) => {
      if (key !== DEFAULT_PATH && !(key in sample.adapters)) pushValue(next, key, 0);
    });
    series = next;
    emit();
  },
  reset() {
    series = new Map();
    emit();
  },
  get(): Series {
    return series;
  },
};

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useSpeedSeries(): Series {
  return useSyncExternalStore(subscribe, () => series, () => series);
}

export function storeVersion(): number {
  return version;
}

export function useNetwork() {
  return useQuery({
    queryKey: ["network"],
    queryFn: async () => {
      const snapshot = await api.get<NetworkSnapshot>("/network");
      speedStore.seed(snapshot);
      return snapshot;
    },
    retry: false,
  });
}

export function useNetworkActions() {
  const client = useQueryClient();
  const apply = (snapshot: NetworkSnapshot) => {
    client.setQueryData(["network"], snapshot);
  };
  const refresh = useMutation({
    mutationFn: () => api.post<NetworkSnapshot>("/network/refresh"),
    onSuccess: apply,
  });
  const toggle = useMutation({
    mutationFn: (multi_path: boolean) => api.put("/settings", { network: { multi_path } }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["network"] });
      void client.invalidateQueries({ queryKey: ["settings"] });
    },
  });
  return { refresh, toggle };
}
