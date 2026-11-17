// Query and mutation hooks. Query keys start with a family name
// ("downloads", "files", …) that real-time events invalidate.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./api";
import { useLiveInterval } from "./realtime";
import type {
  ActivityItem,
  AdminUser,
  Download,
  DownloadAction,
  DownloadStats,
  FileListing,
  Health,
  Invitation,
  LinkInfo,
  LogEntry,
  Page,
  Provider,
  SessionInfo,
  SettingsPayload,
  StorageUsage,
  SubmitResult,
  UpdateStatus,
} from "./types";

export interface DownloadListParams {
  page?: number;
  page_size?: number;
  state?: string;
  provider?: string;
  q?: string;
  sort?: string;
  order?: "asc" | "desc";
  owner?: string;
}

export function useDownloads(params: DownloadListParams, options: { enabled?: boolean } = {}) {
  const refetchInterval = useLiveInterval();
  return useQuery({
    queryKey: ["downloads", "list", params],
    queryFn: () => api.get<Page<Download>>("/downloads", { ...params }),
    placeholderData: keepPreviousData,
    refetchInterval,
    enabled: options.enabled ?? true,
  });
}

export function useDownloadStats(scope: "me" | "all") {
  const refetchInterval = useLiveInterval();
  return useQuery({
    queryKey: ["downloads", "stats", scope],
    queryFn: () => api.get<DownloadStats>("/downloads/stats", { scope }),
    refetchInterval,
  });
}

function useInvalidate() {
  const client = useQueryClient();
  return (...families: string[]) => families.forEach((name) => void client.invalidateQueries({ queryKey: [name] }));
}

export function useDownloadAction() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, action }: { id: number; action: DownloadAction }) =>
      api.post<Download>(`/downloads/${id}/${action}`),
    onSettled: () => invalidate("downloads"),
  });
}

export function useRemoveDownload() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, deleteFile }: { id: number; deleteFile: boolean }) =>
      api.delete<{ ok: boolean }>(`/downloads/${id}`, { delete_file: deleteFile }),
    onSettled: () => invalidate("downloads", "files", "me"),
  });
}

export function useBulkAction() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: { ids: number[]; action: DownloadAction | "remove"; delete_files?: boolean }) =>
      api.post<{ done: number[]; skipped: number[] }>("/downloads/bulk", body),
    onSettled: () => invalidate("downloads", "files", "me"),
  });
}

export function useSetPriority() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, priority }: { id: number; priority: number }) =>
      api.patch<Download>(`/downloads/${id}`, { priority }),
    onSettled: () => invalidate("downloads"),
  });
}

export function useReorder() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (ids: number[]) => api.post<{ ok: boolean }>("/downloads/reorder", { ids }),
    onSettled: () => invalidate("downloads"),
  });
}

export function useInspect() {
  return useMutation({
    mutationFn: (urls: string[]) => api.post<{ items: LinkInfo[] }>("/downloads/inspect", { urls }),
  });
}

export function useSubmit() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: { urls: string[]; destination: string; folder: string; priority: number }) =>
      api.post<SubmitResult>("/downloads", body),
    onSettled: () => invalidate("downloads", "me"),
  });
}

export interface FileListParams {
  scope: "private" | "shared";
  path: string;
  q?: string;
  type?: string;
  sort?: "name" | "size" | "modified";
  order?: "asc" | "desc";
  owner_id?: number | null;
}

export function useFiles(params: FileListParams) {
  return useQuery({
    queryKey: ["files", "list", params],
    queryFn: () => api.get<FileListing>("/files", { ...params, page_size: 1000 }),
    placeholderData: keepPreviousData,
  });
}

export function useStorageUsage() {
  return useQuery({ queryKey: ["files", "usage"], queryFn: () => api.get<StorageUsage>("/files/usage") });
}

export function useProviders() {
  return useQuery({ queryKey: ["providers"], queryFn: () => api.get<Provider[]>("/providers"), staleTime: 30_000 });
}

export interface ActivityParams {
  page?: number;
  page_size?: number;
  category?: string;
  level?: string;
  scope?: "me" | "all";
  q?: string;
}

export function useActivity(params: ActivityParams) {
  return useQuery({
    queryKey: ["activity", params],
    queryFn: () => api.get<Page<ActivityItem>>("/activity", { ...params }),
    placeholderData: keepPreviousData,
  });
}

export function useUsers(enabled = true) {
  return useQuery({ queryKey: ["users", "list"], queryFn: () => api.get<AdminUser[]>("/users"), enabled });
}

export function useInvitations() {
  return useQuery({ queryKey: ["users", "invitations"], queryFn: () => api.get<Invitation[]>("/invitations") });
}

export function useAllSessions() {
  return useQuery({ queryKey: ["users", "sessions"], queryFn: () => api.get<SessionInfo[]>("/admin/sessions") });
}

export function useMySessions() {
  return useQuery({ queryKey: ["me", "sessions"], queryFn: () => api.get<SessionInfo[]>("/auth/sessions") });
}

export function useSettings(enabled = true) {
  return useQuery({ queryKey: ["settings", "values"], queryFn: () => api.get<SettingsPayload>("/settings"), enabled });
}

export function useHealth() {
  return useQuery({
    queryKey: ["settings", "health"],
    queryFn: () => api.get<Health>("/admin/health"),
    refetchInterval: 15_000,
  });
}

export function useLogs(params: { level: string; q: string; limit: number }) {
  return useQuery({
    queryKey: ["settings", "logs", params],
    queryFn: () => api.get<{ items: LogEntry[]; file: string }>("/admin/logs", params),
    placeholderData: keepPreviousData,
  });
}

/** Update status for administrators. The server caches the GitHub answer. */
export function useUpdateStatus(enabled = true) {
  return useQuery({
    queryKey: ["settings", "update"],
    queryFn: () => api.get<UpdateStatus>("/admin/update"),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
}
