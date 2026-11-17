import { ArrowDown, ArrowUp, ChevronsUp, ListOrdered, Plus } from "lucide-react";
import { memo, useMemo, useState } from "react";

import { DownloadActions } from "@/components/downloads/actions";
import { DownloadDetails } from "@/components/downloads/DownloadDetails";
import { DownloadProgress, ProviderTag, speedText, StateBadge, useLive, WaitNote } from "@/components/downloads/parts";
import { useAddDownload } from "@/components/layout/AppShell";
import { Button, Card, EmptyState, ErrorNote, PageHeader, PageLoader, Select, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { formatBytes, formatDuration, PRIORITIES, priorityLabel } from "@/lib/format";
import { useDownloads, useReorder, useSetPriority } from "@/lib/queries";
import type { Download } from "@/lib/types";

const RUNNING = "inspecting,downloading,verifying";
const WAITING = "pending,queued,waiting,paused";

const RunningRow = memo(function RunningRow({ download, onOpen }: { download: Download; onOpen: (id: number) => void }) {
  const live = useLive(download);
  return (
    <li className="flex items-center gap-4 px-4 py-3">
      <div className="min-w-0 flex-1">
        <button type="button" className="block max-w-full truncate text-left text-[13px] font-medium text-fg hover:underline" onClick={() => onOpen(download.id)}>
          {download.filename || download.url}
        </button>
        <div className="mt-0.5 flex items-center gap-3 text-xs text-subtle">
          <ProviderTag id={download.provider} />
        </div>
      </div>
      <div className="hidden w-56 sm:block">
        <DownloadProgress download={download} live={live} />
      </div>
      <div className="tabular hidden w-28 text-right text-xs text-muted md:block">
        <div>{speedText(download, live)}</div>
        <div className="text-subtle">{download.state === "downloading" ? `${formatDuration(live.eta)} left` : ""}</div>
      </div>
      <StateBadge state={download.state} />
      <DownloadActions download={download} />
    </li>
  );
});

interface QueueRowProps {
  download: Download;
  index: number;
  rank: number;
  count: number;
  busy: boolean;
  onMove: (index: number, to: number) => void;
  onPriority: (download: Download, priority: number) => void;
  onOpen: (id: number) => void;
}

function QueueRow({ download, index, rank, count, busy, onMove, onPriority, onOpen }: QueueRowProps) {
  const name = download.filename || download.url;
  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      <span className="tabular w-6 shrink-0 text-right text-xs text-subtle">{rank}</span>
      <div className="min-w-0 flex-1">
        <button type="button" className="block max-w-full truncate text-left text-[13px] font-medium text-fg hover:underline" onClick={() => onOpen(download.id)}>
          {name}
        </button>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-subtle">
          <ProviderTag id={download.provider} />
          {download.total_bytes ? <span className="tabular">{formatBytes(download.total_bytes)}</span> : null}
          <WaitNote download={download} />
          {download.state === "waiting" && download.wait_reason && <span className="truncate">{download.wait_reason}</span>}
        </div>
      </div>
      <span className="hidden sm:block">
        <StateBadge state={download.state} />
      </span>
      <Select
        aria-label={`Priority of ${name}`}
        className="hidden w-28 sm:inline-flex"
        value={download.priority}
        onChange={(event) => onPriority(download, Number(event.target.value))}
      >
        {PRIORITIES.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </Select>
      <div className="flex items-center">
        <Button variant="ghost" size="icon-sm" aria-label={`Move ${name} to the top`} title="Move to top" disabled={busy || index === 0} onClick={() => onMove(index, 0)}>
          <ChevronsUp className="h-3.5 w-3.5" />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label={`Move ${name} up`} title="Move up" disabled={busy || index === 0} onClick={() => onMove(index, index - 1)}>
          <ArrowUp className="h-3.5 w-3.5" />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label={`Move ${name} down`} title="Move down" disabled={busy || index === count - 1} onClick={() => onMove(index, index + 1)}>
          <ArrowDown className="h-3.5 w-3.5" />
        </Button>
      </div>
      <DownloadActions download={download} inline={false} />
    </li>
  );
}

export function QueuePage() {
  const toast = useToast();
  const openAdd = useAddDownload();

  const running = useDownloads({ state: RUNNING, sort: "created_at", order: "asc", page_size: 100 });
  const waiting = useDownloads({ state: WAITING, sort: "queue", order: "asc", page_size: 200 });
  const reorder = useReorder();
  const setPriority = useSetPriority();
  const [openId, setOpenId] = useState<number | null>(null);

  const runningItems = running.data?.items ?? [];
  const waitingItems = useMemo(() => waiting.data?.items ?? [], [waiting.data]);

  const groups = useMemo(() => {
    const map = new Map<number, Download[]>();
    for (const item of waitingItems) {
      const list = map.get(item.priority) ?? [];
      list.push(item);
      map.set(item.priority, list);
    }
    return [...map.entries()].sort((a, b) => b[0] - a[0]);
  }, [waitingItems]);

  const move = (group: Download[], index: number, to: number) => {
    const ids = group.map((item) => item.id);
    const [id] = ids.splice(index, 1);
    ids.splice(to, 0, id);
    reorder.mutate(ids, { onError: (error) => toast.error(error) });
  };

  const changePriority = (download: Download, priority: number) =>
    setPriority.mutate({ id: download.id, priority }, { onError: (error) => toast.error(error) });

  const all = [...runningItems, ...waitingItems];
  const openDownload = all.find((item) => item.id === openId) ?? null;
  const truncated = (waiting.data?.total ?? 0) > waitingItems.length;
  let rank = 0;

  return (
    <>
      <PageHeader
        title="Queue"
        description="Downloads start in this order: higher priority first, then top to bottom."
        actions={
          <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => openAdd()}>
            Add download
          </Button>
        }
      />

      {(running.isError || waiting.isError) && (
        <ErrorNote className="mb-4">{errorMessage(running.error ?? waiting.error)}</ErrorNote>
      )}

      <section aria-labelledby="running-heading" className="mb-6">
        <h2 id="running-heading" className="mb-3 text-[13px] font-semibold text-fg">
          Running <span className="tabular ml-1 font-normal text-subtle">{runningItems.length}</span>
        </h2>
        <Card>
          {running.isPending ? (
            <PageLoader />
          ) : runningItems.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-subtle">Nothing is running right now.</p>
          ) : (
            <ul className="divide-y divide-line">
              {runningItems.map((download) => (
                <RunningRow key={download.id} download={download} onOpen={setOpenId} />
              ))}
            </ul>
          )}
        </Card>
      </section>

      <section aria-labelledby="waiting-heading">
        <h2 id="waiting-heading" className="mb-3 text-[13px] font-semibold text-fg">
          Up next <span className="tabular ml-1 font-normal text-subtle">{waiting.data?.total ?? 0}</span>
        </h2>
        {waiting.isPending ? (
          <Card>
            <PageLoader />
          </Card>
        ) : waitingItems.length === 0 ? (
          <Card>
            <EmptyState icon={<ListOrdered className="h-5 w-5" />} title="The queue is empty" description="Queued, paused and waiting downloads appear here." />
          </Card>
        ) : (
          <div className="space-y-4">
            {groups.map(([priority, group]) => (
              <Card key={priority} className="overflow-visible">
                <div className="flex items-center justify-between border-b border-line bg-raised/40 px-4 py-2">
                  <span className="text-2xs font-medium uppercase tracking-wider text-subtle">{priorityLabel(priority)} priority</span>
                  <span className="tabular text-2xs text-subtle">{group.length}</span>
                </div>
                <ul className="divide-y divide-line">
                  {group.map((download, index) => {
                    rank += 1;
                    return (
                      <QueueRow
                        key={download.id}
                        download={download}
                        index={index}
                        rank={rank}
                        count={group.length}
                        busy={reorder.isPending}
                        onMove={(from, to) => move(group, from, to)}
                        onPriority={changePriority}
                        onOpen={setOpenId}
                      />
                    );
                  })}
                </ul>
              </Card>
            ))}
            {truncated && <p className="text-xs text-subtle">Showing the first {waitingItems.length} queued downloads.</p>}
          </div>
        )}
      </section>

      <DownloadDetails download={openDownload} onClose={() => setOpenId(null)} />
    </>
  );
}
