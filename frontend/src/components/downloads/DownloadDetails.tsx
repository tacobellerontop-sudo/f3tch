import { Copy } from "lucide-react";
import type { ReactNode } from "react";

import { Button, Dialog, ErrorNote, Notice, useToast } from "@/components/ui";
import { formatBytes, formatDateTime, formatDuration, formatSpeed, priorityLabel } from "@/lib/format";
import type { Download } from "@/lib/types";

import { DownloadActions } from "./actions";
import { DownloadProgress, providerLabel, StateBadge, useLive, WaitNote } from "./parts";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-6 py-1.5">
      <dt className="shrink-0 text-xs text-subtle">{label}</dt>
      <dd className="tabular min-w-0 truncate text-right text-[13px] text-fg">{children}</dd>
    </div>
  );
}

interface Props {
  download: Download | null;
  currentUserId: number;
  showOwner: boolean;
  onClose: () => void;
}

export function DownloadDetails({ download, currentUserId, showOwner, onClose }: Props) {
  return (
    <Dialog open={download !== null} onClose={onClose} title={download?.filename || "Download"} side>
      {download && <Body download={download} currentUserId={currentUserId} showOwner={showOwner} />}
    </Dialog>
  );
}

function Body({ download, currentUserId, showOwner }: { download: Download; currentUserId: number; showOwner: boolean }) {
  const live = useLive(download);
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(download.url);
      toast.success("Download URL copied.");
    } catch {
      toast.error("The browser did not allow copying.");
    }
  };
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <StateBadge state={download.state} />
        <DownloadActions download={download} currentUserId={currentUserId} />
      </div>
      <DownloadProgress download={download} live={live} />
      {download.state === "waiting" && (
        <Notice tone="warning">
          <p>{download.wait_reason || "Waiting for the provider."}</p>
          <p className="mt-1">
            <WaitNote download={download} />
          </p>
        </Notice>
      )}
      {download.state === "failed" && <ErrorNote>{download.error_message || "The download failed."}</ErrorNote>}

      <dl className="divide-y divide-line border-y border-line">
        <Row label="Downloaded">
          {formatBytes(live.downloaded)}
          {live.total ? ` of ${formatBytes(live.total)}` : ""}
        </Row>
        <Row label="Progress">{live.percent === null ? "Unknown size" : `${live.percent.toFixed(1)}%`}</Row>
        <Row label="Current speed">{download.state === "downloading" ? formatSpeed(live.speed) : "—"}</Row>
        <Row label="Average speed">{formatSpeed(live.average)}</Row>
        <Row label="Time remaining">{download.state === "downloading" ? formatDuration(live.eta) : "—"}</Row>
        <Row label="Provider">{providerLabel(download.provider)}</Row>
        <Row label="Engine">{download.engine === "aria2" ? "aria2 (multi-connection)" : download.engine === "native" ? "Native" : "—"}</Row>
        <Row label="Priority">{priorityLabel(download.priority)}</Row>
        <Row label="Destination">
          {download.destination === "shared" ? "Shared folder" : "My files"}
          {download.folder ? ` / ${download.folder}` : ""}
        </Row>
        {showOwner && <Row label="Owner">{download.owner}</Row>}
        <Row label="Added">{formatDateTime(download.created_at)}</Row>
        <Row label="Started">{formatDateTime(download.started_at)}</Row>
        <Row label="Finished">{formatDateTime(download.finished_at)}</Row>
        {download.attempts > 0 && (
          <Row label="Retries">
            {download.attempts} of {download.max_attempts}
          </Row>
        )}
      </dl>

      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <p className="text-xs font-medium text-muted">Source URL</p>
          <Button variant="ghost" size="sm" icon={<Copy className="h-3 w-3" />} onClick={() => void copy()}>
            Copy
          </Button>
        </div>
        <p className="select-all break-all rounded border border-line bg-canvas px-2.5 py-2 font-mono text-xs text-muted">
          {download.url}
        </p>
      </div>
    </div>
  );
}
