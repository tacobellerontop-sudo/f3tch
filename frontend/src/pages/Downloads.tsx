import { ArrowDown, ArrowUp, Download as DownloadIcon, Pause, Play, Plus, RotateCw, Search, Trash2, X } from "lucide-react";
import { memo, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { canDo, DownloadActions } from "@/components/downloads/actions";
import { DownloadDetails } from "@/components/downloads/DownloadDetails";
import {
  ALL_STATES,
  DownloadProgress,
  ProviderTag,
  speedText,
  STATE_META,
  StateBadge,
  useLive,
  WaitNote,
} from "@/components/downloads/parts";
import { useAddDownload } from "@/components/layout/AppShell";
import {
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  EmptyState,
  ErrorNote,
  Input,
  PageHeader,
  PageLoader,
  Pagination,
  Select,
  useToast,
} from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { useMe } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatBytes, formatDuration, formatRelative } from "@/lib/format";
import { useBulkAction, useDownloads, useProviders, useUsers } from "@/lib/queries";
import type { Download, DownloadAction } from "@/lib/types";

const PAGE_SIZE = 50;
const ACTIVE = "inspecting,downloading,verifying,waiting";

type SortKey = "created_at" | "filename" | "size" | "state" | "provider" | "progress";

function useDebounced<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

interface RowProps {
  download: Download;
  selected: boolean;
  showOwner: boolean;
  currentUserId: number;
  onToggle: (id: number) => void;
  onOpen: (id: number) => void;
}

const Row = memo(function Row({ download, selected, showOwner, currentUserId, onToggle, onOpen }: RowProps) {
  const live = useLive(download);
  const name = download.filename || download.url;
  return (
    <tr
      className={cn(
        "group cursor-pointer border-t border-line transition-colors hover:bg-raised/60",
        selected && "bg-accent/5",
      )}
      onClick={() => onOpen(download.id)}
    >
      <td className="w-9 py-2.5 pl-4" onClick={(event) => event.stopPropagation()}>
        <Checkbox aria-label={`Select ${name}`} checked={selected} onChange={() => onToggle(download.id)} />
      </td>
      <td className="max-w-0 py-2.5 pl-2 pr-4">
        <button
          type="button"
          className="block w-full truncate text-left text-[13px] font-medium text-fg hover:underline focus-visible:underline"
          title={name}
          onClick={(event) => {
            event.stopPropagation();
            onOpen(download.id);
          }}
        >
          {name}
        </button>
        <div className="mt-0.5 flex items-center gap-3 text-xs text-subtle">
          <ProviderTag id={download.provider} className="md:hidden" />
          {showOwner && <span className="truncate">{download.owner}</span>}
          {download.state === "failed" && download.error_message && (
            <span className="truncate text-danger" title={download.error_message}>
              {download.error_message}
            </span>
          )}
          <WaitNote download={download} />
          <span className="lg:hidden">
            <StateBadge state={download.state} />
          </span>
        </div>
        <div className="mt-2 sm:hidden">
          <DownloadProgress download={download} live={live} />
        </div>
      </td>
      <td className="hidden w-32 py-2.5 pr-4 md:table-cell">
        <ProviderTag id={download.provider} />
      </td>
      <td className="hidden w-32 py-2.5 pr-4 lg:table-cell">
        <StateBadge state={download.state} />
      </td>
      <td className="hidden w-48 py-2.5 pr-4 sm:table-cell">
        <DownloadProgress download={download} live={live} />
      </td>
      <td className="tabular hidden w-24 py-2.5 pr-4 text-right text-xs text-muted xl:table-cell">
        {speedText(download, live)}
      </td>
      <td className="tabular hidden w-24 py-2.5 pr-4 text-right text-xs text-muted xl:table-cell">
        {download.state === "downloading" ? formatDuration(live.eta) : "—"}
      </td>
      <td className="tabular hidden w-24 py-2.5 pr-4 text-right text-xs text-muted 2xl:table-cell">
        {formatBytes(live.total)}
      </td>
      <td className="tabular hidden w-24 py-2.5 pr-4 text-right text-xs text-subtle 2xl:table-cell">
        {formatRelative(download.created_at)}
      </td>
      <td className="w-20 py-2.5 pr-3">
        <DownloadActions download={download} currentUserId={currentUserId} />
      </td>
    </tr>
  );
});

export function DownloadsPage() {
  const me = useMe();
  const toast = useToast();
  const openAdd = useAddDownload();
  const isAdmin = me.user.role === "admin";
  const [params, setParams] = useSearchParams();

  const state = params.get("state") ?? "";
  const provider = params.get("provider") ?? "";
  const owner = params.get("owner") ?? (isAdmin ? "" : "me");
  const sort = (params.get("sort") as SortKey | null) ?? "created_at";
  const order = params.get("order") === "asc" ? "asc" : "desc";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const [search, setSearch] = useState(params.get("q") ?? "");
  const q = useDebounced(search.trim());

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!("page" in changes)) next.delete("page");
    setParams(next, { replace: true });
  };

  useEffect(() => {
    if ((params.get("q") ?? "") !== q) update({ q });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const list = useDownloads({
    page,
    page_size: PAGE_SIZE,
    state: state === "active" ? ACTIVE : state,
    provider,
    q,
    sort,
    order,
    owner: isAdmin ? owner : "me",
  });
  const providers = useProviders();
  const users = useUsers(isAdmin);
  const bulk = useBulkAction();

  const items = useMemo(() => list.data?.items ?? [], [list.data]);
  const total = list.data?.total ?? 0;
  const showOwner = isAdmin && owner !== "me";

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [openId, setOpenId] = useState<number | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [deleteFiles, setDeleteFiles] = useState(false);

  // Drop selections that are no longer on the page.
  useEffect(() => {
    setSelected((current) => {
      const visible = new Set(items.map((item) => item.id));
      const next = new Set([...current].filter((id) => visible.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [items]);

  const toggle = (id: number) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const allSelected = items.length > 0 && items.every((item) => selected.has(item.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(items.map((item) => item.id)));

  const chosen = items.filter((item) => selected.has(item.id));
  const count = (action: DownloadAction) => chosen.filter((item) => canDo(action, item.state)).length;

  const runBulk = (action: DownloadAction | "remove") => {
    const ids = chosen.filter((item) => action === "remove" || canDo(action, item.state)).map((item) => item.id);
    if (ids.length === 0) return;
    bulk.mutate(
      { ids, action, delete_files: action === "remove" ? deleteFiles : undefined },
      {
        onSuccess: (result) => {
          const verb = { pause: "Paused", resume: "Resumed", cancel: "Cancelled", retry: "Retrying", remove: "Removed" }[action];
          const skipped = result.skipped.length ? ` ${result.skipped.length} skipped.` : "";
          toast.success(`${verb} ${result.done.length} download${result.done.length === 1 ? "" : "s"}.${skipped}`);
          setSelected(new Set());
        },
        onError: (error) => toast.error(error),
        onSettled: () => setConfirmRemove(false),
      },
    );
  };

  const sortBy = (key: SortKey) => {
    if (sort === key) update({ order: order === "asc" ? "desc" : "asc" });
    else update({ sort: key, order: key === "filename" || key === "provider" ? "asc" : "desc" });
  };

  const Th = ({ label, sortKey, className }: { label: string; sortKey?: SortKey; className?: string }) => (
    <th
      scope="col"
      className={cn("py-2 pr-4 text-left text-2xs font-medium uppercase tracking-wider text-subtle", className)}
      aria-sort={sortKey && sort === sortKey ? (order === "asc" ? "ascending" : "descending") : undefined}
    >
      {sortKey ? (
        <button type="button" className="inline-flex items-center gap-1 uppercase tracking-wider hover:text-fg" onClick={() => sortBy(sortKey)}>
          {label}
          {sort === sortKey && (order === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
        </button>
      ) : (
        label
      )}
    </th>
  );

  const filtered = Boolean(state || provider || q || (isAdmin && owner));
  const openDownload = items.find((item) => item.id === openId) ?? null;

  return (
    <>
      <PageHeader
        title="Downloads"
        description={`${total} download${total === 1 ? "" : "s"}${filtered ? " match the filters" : ""}.`}
        actions={
          <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => openAdd()}>
            Add download
          </Button>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[12rem] flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <Input
            type="search"
            aria-label="Search downloads"
            placeholder="Search by name or URL"
            className="pl-8"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <Select aria-label="Filter by status" value={state} onChange={(event) => update({ state: event.target.value })}>
          <option value="">All statuses</option>
          <option value="active">Active</option>
          {ALL_STATES.map((value) => (
            <option key={value} value={value}>
              {STATE_META[value].label}
            </option>
          ))}
        </Select>
        <Select aria-label="Filter by provider" value={provider} onChange={(event) => update({ provider: event.target.value })}>
          <option value="">All providers</option>
          {(providers.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </Select>
        {isAdmin && (
          <Select aria-label="Filter by user" value={owner} onChange={(event) => update({ owner: event.target.value })}>
            <option value="">Everyone</option>
            <option value="me">Mine</option>
            {(users.data ?? [])
              .filter((user) => user.id !== me.user.id)
              .map((user) => (
                <option key={user.id} value={String(user.id)}>
                  {user.username}
                </option>
              ))}
          </Select>
        )}
        {filtered && (
          <Button
            variant="ghost"
            size="sm"
            icon={<X className="h-3.5 w-3.5" />}
            onClick={() => {
              setSearch("");
              setParams(new URLSearchParams(), { replace: true });
            }}
          >
            Clear
          </Button>
        )}
      </div>

      {selected.size > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-accent/25 bg-accent/5 px-3 py-2" role="toolbar" aria-label="Bulk actions">
          <span className="mr-2 text-xs font-medium text-fg">{selected.size} selected</span>
          <Button size="sm" variant="secondary" icon={<Pause className="h-3.5 w-3.5" />} disabled={!count("pause") || bulk.isPending} onClick={() => runBulk("pause")}>
            Pause
          </Button>
          <Button size="sm" variant="secondary" icon={<Play className="h-3.5 w-3.5" />} disabled={!count("resume") || bulk.isPending} onClick={() => runBulk("resume")}>
            Resume
          </Button>
          <Button size="sm" variant="secondary" icon={<RotateCw className="h-3.5 w-3.5" />} disabled={!count("retry") || bulk.isPending} onClick={() => runBulk("retry")}>
            Retry
          </Button>
          <Button
            size="sm"
            variant="secondary"
            icon={<Trash2 className="h-3.5 w-3.5" />}
            disabled={bulk.isPending}
            onClick={() => {
              setDeleteFiles(false);
              setConfirmRemove(true);
            }}
          >
            Remove
          </Button>
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setSelected(new Set())}>
            Clear selection
          </Button>
        </div>
      )}

      <Card className="overflow-hidden">
        {list.isPending ? (
          <PageLoader />
        ) : list.isError ? (
          <div className="p-4">
            <ErrorNote>{errorMessage(list.error)}</ErrorNote>
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<DownloadIcon className="h-5 w-5" />}
            title={filtered ? "No downloads match these filters" : "No downloads yet"}
            description={filtered ? "Change or clear the filters to see more." : "Paste one or more links to get started."}
            action={
              filtered ? undefined : (
                <Button variant="secondary" icon={<Plus className="h-4 w-4" />} onClick={() => openAdd()}>
                  Add download
                </Button>
              )
            }
          />
        ) : (
          <>
            <table className="w-full table-fixed border-collapse">
              <thead className="bg-raised/40">
                <tr>
                  <th scope="col" className="w-9 py-2 pl-4 text-left">
                    <Checkbox aria-label="Select all on this page" checked={allSelected} onChange={toggleAll} />
                  </th>
                  <Th label="Name" sortKey="filename" className="pl-2" />
                  <Th label="Provider" sortKey="provider" className="hidden w-32 md:table-cell" />
                  <Th label="Status" sortKey="state" className="hidden w-32 lg:table-cell" />
                  <Th label="Progress" sortKey="progress" className="hidden w-48 sm:table-cell" />
                  <Th label="Speed" className="hidden w-24 text-right xl:table-cell" />
                  <Th label="ETA" className="hidden w-24 text-right xl:table-cell" />
                  <Th label="Size" sortKey="size" className="hidden w-24 text-right 2xl:table-cell" />
                  <Th label="Added" sortKey="created_at" className="hidden w-24 text-right 2xl:table-cell" />
                  <th scope="col" className="w-20 py-2 pr-3">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((download) => (
                  <Row
                    key={download.id}
                    download={download}
                    selected={selected.has(download.id)}
                    showOwner={showOwner}
                    currentUserId={me.user.id}
                    onToggle={toggle}
                    onOpen={setOpenId}
                  />
                ))}
              </tbody>
            </table>
            <div className="border-t border-line">
              <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPage={(next) => update({ page: next > 1 ? String(next) : "" })} />
            </div>
          </>
        )}
      </Card>

      <DownloadDetails download={openDownload} currentUserId={me.user.id} showOwner={isAdmin} onClose={() => setOpenId(null)} />

      <ConfirmDialog
        open={confirmRemove}
        onClose={() => setConfirmRemove(false)}
        title={`Remove ${selected.size} download${selected.size === 1 ? "" : "s"}?`}
        message="They will be removed from the list. Downloads that are still running are stopped first."
        confirmLabel="Remove"
        destructive
        loading={bulk.isPending}
        onConfirm={() => runBulk("remove")}
      >
        <Checkbox label="Also delete the downloaded files" checked={deleteFiles} onChange={(event) => setDeleteFiles(event.target.checked)} />
      </ConfirmDialog>
    </>
  );
}
