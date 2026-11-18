import {
  AlertCircle,
  Ban,
  CheckCircle2,
  CircleDashed,
  Clock,
  Cloud,
  Download as DownloadIcon,
  HardDrive,
  Hourglass,
  Link2,
  Pause,
  Search,
  ShieldCheck,
  Video,
  Zap,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Badge, ProgressBar } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatBytes, formatDuration, formatPercent, formatSpeed } from "@/lib/format";
import { useProgress } from "@/lib/progress";
import type { Download, DownloadState } from "@/lib/types";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

export const STATE_META: Record<DownloadState, { label: string; tone: Tone; icon: ReactNode }> = {
  pending: { label: "Pending", tone: "neutral", icon: <CircleDashed className="h-3 w-3" /> },
  queued: { label: "Queued", tone: "neutral", icon: <Clock className="h-3 w-3" /> },
  inspecting: { label: "Inspecting", tone: "info", icon: <Search className="h-3 w-3" /> },
  waiting: { label: "Waiting", tone: "warning", icon: <Hourglass className="h-3 w-3" /> },
  downloading: { label: "Downloading", tone: "accent", icon: <DownloadIcon className="h-3 w-3" /> },
  paused: { label: "Paused", tone: "neutral", icon: <Pause className="h-3 w-3" /> },
  verifying: { label: "Verifying", tone: "info", icon: <ShieldCheck className="h-3 w-3" /> },
  completed: { label: "Completed", tone: "success", icon: <CheckCircle2 className="h-3 w-3" /> },
  failed: { label: "Failed", tone: "danger", icon: <AlertCircle className="h-3 w-3" /> },
  cancelled: { label: "Cancelled", tone: "neutral", icon: <Ban className="h-3 w-3" /> },
};

export const ALL_STATES = Object.keys(STATE_META) as DownloadState[];

export function StateBadge({ state }: { state: DownloadState }) {
  const meta = STATE_META[state];
  return (
    <Badge tone={meta.tone}>
      {meta.icon}
      {meta.label}
    </Badge>
  );
}

const PROVIDERS: Record<string, { label: string; icon: ReactNode }> = {
  direct: { label: "Direct", icon: <Link2 className="h-3 w-3" /> },
  rapidgator: { label: "Rapidgator", icon: <Zap className="h-3 w-3" /> },
  mega: { label: "MEGA", icon: <Cloud className="h-3 w-3" /> },
  pixeldrain: { label: "Pixeldrain", icon: <Cloud className="h-3 w-3" /> },
  gdrive: { label: "Google Drive", icon: <HardDrive className="h-3 w-3" /> },
  dropbox: { label: "Dropbox", icon: <Cloud className="h-3 w-3" /> },
  mediafire: { label: "MediaFire", icon: <Cloud className="h-3 w-3" /> },
  wetransfer: { label: "WeTransfer", icon: <Cloud className="h-3 w-3" /> },
  media: { label: "YouTube & media", icon: <Video className="h-3 w-3" /> },
};

export function providerLabel(id: string): string {
  return PROVIDERS[id]?.label ?? (id || "Unknown");
}

export function ProviderTag({ id, className }: { id: string; className?: string }) {
  const meta = PROVIDERS[id];
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-muted", className)}>
      <span className="text-subtle">{meta?.icon ?? <Link2 className="h-3 w-3" />}</span>
      {providerLabel(id)}
    </span>
  );
}

export interface LiveDownload {
  downloaded: number;
  total: number | null;
  speed: number;
  average: number;
  eta: number | null;
  percent: number | null;
}

/** Merge a download row with its live progress (when it is transferring). */
export function useLive(download: Download): LiveDownload {
  const live = useProgress(download.id);
  const transferring = download.state === "downloading" && live !== undefined;
  const downloaded = transferring ? Math.max(live.downloaded_bytes, 0) : download.downloaded_bytes;
  const total = transferring ? (live.total_bytes ?? download.total_bytes) : download.total_bytes;
  return {
    downloaded,
    total,
    speed: transferring ? live.speed_bps : download.state === "downloading" ? download.speed_bps : 0,
    average: transferring ? live.avg_speed_bps : download.avg_speed_bps,
    eta: transferring ? live.eta_seconds : download.state === "downloading" ? download.eta_seconds : null,
    percent: download.state === "completed" ? 100 : formatPercent(downloaded, total),
  };
}

const barTone: Record<DownloadState, "accent" | "success" | "warning" | "danger" | "neutral"> = {
  pending: "neutral",
  queued: "neutral",
  inspecting: "accent",
  waiting: "warning",
  downloading: "accent",
  paused: "neutral",
  verifying: "accent",
  completed: "success",
  failed: "danger",
  cancelled: "neutral",
};

export function DownloadProgress({ download, live, compact }: { download: Download; live: LiveDownload; compact?: boolean }) {
  const busy = download.state === "inspecting" || download.state === "verifying" || download.state === "downloading";
  return (
    <div className="min-w-0">
      <ProgressBar
        value={download.state === "inspecting" || download.state === "verifying" ? null : live.percent}
        active={busy}
        tone={barTone[download.state]}
        label={`Progress of ${download.filename || "download"}`}
      />
      {!compact && (
        <div className="tabular mt-1.5 flex items-center justify-between gap-2 text-2xs text-subtle">
          <span className="truncate">
            {formatBytes(live.downloaded)}
            {live.total ? ` / ${formatBytes(live.total)}` : ""}
          </span>
          <span>{live.percent === null ? "" : `${live.percent.toFixed(live.percent < 100 ? 1 : 0)}%`}</span>
        </div>
      )}
    </div>
  );
}

/** Seconds until `iso`, ticking once a second while in the future. */
export function useCountdown(iso: string | null): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!iso) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [iso]);
  if (!iso) return null;
  return Math.max(0, Math.round((new Date(iso).getTime() - now) / 1000));
}

export function WaitNote({ download }: { download: Download }) {
  const seconds = useCountdown(download.state === "waiting" ? download.wait_until : null);
  if (download.state !== "waiting") return null;
  if (download.error_code === "captcha_required") {
    return <span className="text-xs font-medium text-warning">Needs you: click here to complete the CAPTCHA</span>;
  }
  return (
    <span className="text-xs text-warning">
      {seconds !== null && seconds > 0 ? `Continues in ${formatDuration(seconds)}` : "Continuing…"}
    </span>
  );
}

export function speedText(download: Download, live: LiveDownload): string {
  return download.state === "downloading" ? formatSpeed(live.speed) : "—";
}
