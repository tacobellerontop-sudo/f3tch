import { ArrowDownToLine, CheckCircle2, Clock, Gauge, HardDrive, Plus, XCircle } from "lucide-react";
import { memo, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { DownloadActions } from "@/components/downloads/actions";
import { DownloadProgress, ProviderTag, StateBadge, useLive, WaitNote } from "@/components/downloads/parts";
import { useAddDownload } from "@/components/layout/AppShell";
import { Button, Card, EmptyState, PageHeader, PageLoader, ProgressBar } from "@/components/ui";
import { useBootstrap } from "@/lib/app";
import { cn } from "@/lib/cn";
import { formatBytes, formatDuration, formatRelative, formatSpeed } from "@/lib/format";
import { useSpeedSum } from "@/lib/progress";
import { useActivity, useDownloads, useDownloadStats, useStorageUsage } from "@/lib/queries";
import type { ActivityItem, Download } from "@/lib/types";

const ACTIVE_STATES = "inspecting,downloading,verifying,waiting";

function Stat({ label, value, icon, to, tone }: { label: string; value: ReactNode; icon: ReactNode; to?: string; tone?: string }) {
  const body = (
    <Card className={cn("h-full p-4 transition-colors", to && "hover:border-line-strong")}>
      <div className="flex items-center justify-between text-xs text-muted">
        <span>{label}</span>
        <span className={cn("text-subtle", tone)}>{icon}</span>
      </div>
      <p className="tabular mt-2 text-2xl font-semibold tracking-tight text-fg">{value}</p>
    </Card>
  );
  return to ? <Link to={to} className="block rounded-lg">{body}</Link> : body;
}

const ActiveCard = memo(function ActiveCard({ download }: { download: Download }) {
  const live = useLive(download);
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-fg" title={download.filename || download.url}>
            {download.filename || download.url}
          </p>
          <div className="mt-1 flex items-center gap-3">
            <ProviderTag id={download.provider} />
          </div>
        </div>
        <DownloadActions download={download} />
      </div>
      <div className="mt-4">
        <DownloadProgress download={download} live={live} />
      </div>
      <div className="tabular mt-3 flex items-center justify-between gap-3 text-xs text-muted">
        <StateBadge state={download.state} />
        {download.state === "waiting" ? (
          <WaitNote download={download} />
        ) : download.state === "downloading" ? (
          <span>
            {formatSpeed(live.speed)}
            <span className="mx-1.5 text-subtle">·</span>
            {formatDuration(live.eta)} left
          </span>
        ) : null}
      </div>
    </Card>
  );
});

const LEVEL_DOT: Record<ActivityItem["level"], string> = { info: "bg-subtle", warning: "bg-warning", error: "bg-danger" };

export function ActivityList({ items }: { items: ActivityItem[] }) {
  return (
    <ul className="divide-y divide-line">
      {items.map((item) => (
        <li key={item.id} className="flex items-start gap-3 px-4 py-2.5">
          <span className={cn("mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full", LEVEL_DOT[item.level])} />
          <div className="min-w-0 flex-1">
            <p className="break-words text-[13px] leading-5 text-fg">{item.message}</p>
            <p className="text-2xs text-subtle">{item.category}</p>
          </div>
          <time className="tabular shrink-0 pt-0.5 text-2xs text-subtle" dateTime={item.created_at} title={new Date(item.created_at).toLocaleString()}>
            {formatRelative(item.created_at)}
          </time>
        </li>
      ))}
    </ul>
  );
}

export function OverviewPage() {
  const { usage: figures } = useBootstrap();
  const openAdd = useAddDownload();

  const stats = useDownloadStats();
  const active = useDownloads({ state: ACTIVE_STATES, sort: "created_at", order: "asc", page_size: 24 });
  const activity = useActivity({ page_size: 8 });
  const usage = useStorageUsage();
  const items = active.data?.items ?? [];
  const speed = useSpeedSum(items);

  const used = usage.data?.private.used_bytes ?? figures.storage_bytes;
  const limit = usage.data?.private.limit_bytes ?? figures.storage_limit_bytes;
  const shared = usage.data?.shared.used_bytes ?? 0;
  const disk = usage.data?.disk;

  return (
    <>
      <PageHeader
        title="Overview"
        description="What FETCH is doing on this computer."
        actions={
          <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => openAdd()}>
            Add download
          </Button>
        }
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Active" value={stats.data?.active ?? "–"} icon={<ArrowDownToLine className="h-4 w-4" />} to="/downloads?state=active" tone="text-accent" />
        <Stat label="Queued" value={stats.data?.queued ?? "–"} icon={<Clock className="h-4 w-4" />} to="/queue" />
        <Stat label="Completed" value={stats.data?.completed ?? "–"} icon={<CheckCircle2 className="h-4 w-4" />} to="/downloads?state=completed" tone="text-success" />
        <Stat label="Failed" value={stats.data?.failed ?? "–"} icon={<XCircle className="h-4 w-4" />} to="/downloads?state=failed" tone={stats.data?.failed ? "text-danger" : undefined} />
        <div className="col-span-2 lg:col-span-1">
          <Stat label="Download speed" value={speed > 0 ? formatSpeed(speed) : "Idle"} icon={<Gauge className="h-4 w-4" />} />
        </div>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <section aria-labelledby="active-heading" className="min-w-0">
          <div className="mb-3 flex items-center justify-between">
            <h2 id="active-heading" className="text-[13px] font-semibold text-fg">Active downloads</h2>
            <Link to="/downloads" className="text-xs text-muted hover:text-fg">View all</Link>
          </div>
          {active.isPending ? (
            <Card><PageLoader /></Card>
          ) : items.length === 0 ? (
            <Card>
              <EmptyState
                icon={<ArrowDownToLine className="h-5 w-5" />}
                title="Nothing is downloading"
                description="Paste a link to start. Progress appears here live."
                action={<Button variant="secondary" icon={<Plus className="h-4 w-4" />} onClick={() => openAdd()}>Add download</Button>}
              />
            </Card>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {items.map((download) => (
                <ActiveCard key={download.id} download={download} />
              ))}
            </div>
          )}
        </section>

        <div className="space-y-6">
          <section aria-labelledby="storage-heading">
            <h2 id="storage-heading" className="mb-3 text-[13px] font-semibold text-fg">Storage</h2>
            <Card className="space-y-4 p-4">
              <div>
                <div className="mb-2 flex items-baseline justify-between">
                  <span className="flex items-center gap-2 text-xs text-muted"><HardDrive className="h-3.5 w-3.5 text-subtle" />Downloaded files</span>
                  <span className="tabular text-xs text-fg">
                    {formatBytes(used)}
                    <span className="text-subtle">{limit > 0 ? ` of ${formatBytes(limit, 0)}` : " · no limit"}</span>
                  </span>
                </div>
                <ProgressBar value={limit > 0 ? Math.min(100, (used / limit) * 100) : 0} label="Storage used" tone={limit > 0 && used / limit > 0.9 ? "danger" : "accent"} />
              </div>
              {shared > 0 && (
                <div className="flex items-baseline justify-between text-xs">
                  <span className="text-muted">Shared folder</span>
                  <span className="tabular text-fg">{formatBytes(shared)}</span>
                </div>
              )}
              {disk && disk.total > 0 && (
                <div>
                  <div className="mb-2 flex items-baseline justify-between text-xs">
                    <span className="text-muted">Disk</span>
                    <span className="tabular text-fg">{formatBytes(disk.free)} free<span className="text-subtle"> of {formatBytes(disk.total, 0)}</span></span>
                  </div>
                  <ProgressBar value={(disk.used / disk.total) * 100} tone={disk.used / disk.total > 0.9 ? "danger" : "neutral"} label="Disk used" />
                </div>
              )}
              <div className="tabular flex items-center justify-between border-t border-line pt-3 text-xs text-muted">
                <span>Downloads in the last 24 h</span>
                <span className="text-fg">{figures.downloads_today}</span>
              </div>
            </Card>
          </section>

          <section aria-labelledby="activity-heading">
            <div className="mb-3 flex items-center justify-between">
              <h2 id="activity-heading" className="text-[13px] font-semibold text-fg">Recent activity</h2>
              <Link to="/activity" className="text-xs text-muted hover:text-fg">View all</Link>
            </div>
            <Card>
              {activity.isPending ? (
                <PageLoader />
              ) : (activity.data?.items.length ?? 0) === 0 ? (
                <EmptyState title="No activity yet" />
              ) : (
                <ActivityList items={activity.data!.items} />
              )}
            </Card>
          </section>
        </div>
      </div>
    </>
  );
}
