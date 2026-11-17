// Query and mutation hooks. Query keys start with a family name
// ("downloads", "files", …) that real-time events invalidate.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./api";
import { useLiveInterval } from "./realtime";
import type {
  ActivityItem,
  CaptchaChallenge,
  Download,
  DownloadAction,
  DownloadStats,
  FileListing,
  Health,
  LinkInfo,
  LogEntry,
  Page,
  Provider,
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

export function useDownloadStats() {
  const refetchInterval = useLiveInterval();
  return useQuery({
    queryKey: ["downloads", "stats"],
    queryFn: () => api.get<DownloadStats>("/downloads/stats"),
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

export function isAwaitingCaptcha(download: Pick<Download, "state" | "error_code">): boolean {
  return download.state === "waiting" && download.error_code === "captcha_required";
}

export function useCaptcha(downloadId: number | null) {
  return useQuery({
    queryKey: ["downloads", "captcha", downloadId],
    queryFn: () => api.get<CaptchaChallenge>(`/downloads/${downloadId}/captcha`),
    enabled: downloadId !== null,
    retry: false,
    staleTime: 0,
  });
}

export function useSubmitCaptcha() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number; answer?: string; download_url?: string }) =>
      api.post<Download>(`/downloads/${id}/captcha`, body),
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
    onSettled: () => invalidate("downloads", "bootstrap"),
  });
}

export interface FileListParams {
  scope: "private" | "shared";
  path: string;
  q?: string;
  type?: string;
  sort?: "name" | "size" | "modified";
  order?: "asc" | "desc";
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
  q?: string;
}

export function useActivity(params: ActivityParams) {
  return useQuery({
    queryKey: ["activity", params],
    queryFn: () => api.get<Page<ActivityItem>>("/activity", { ...params }),
    placeholderData: keepPreviousData,
  });
}

export function useSettings() {
  return useQuery({ queryKey: ["settings", "values"], queryFn: () => api.get<SettingsPayload>("/settings") });
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

/** Update status. The server caches the GitHub answer. */
export function useUpdateStatus() {
  return useQuery({
    queryKey: ["settings", "update"],
    queryFn: () => api.get<UpdateStatus>("/admin/update"),
    staleTime: 5 * 60_000,
    retry: false,
  });
}
