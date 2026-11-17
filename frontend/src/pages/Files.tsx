import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  ChevronRight,
  Download as DownloadIcon,
  File as FileIcon,
  FileArchive,
  FileText,
  Film,
  Folder,
  FolderOpen,
  FolderPlus,
  Image as ImageIcon,
  Music,
  Package,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

import { providerLabel } from "@/components/downloads/parts";
import { useAddDownload } from "@/components/layout/AppShell";
import {
  Button,
  Card,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorNote,
  Field,
  Input,
  PageHeader,
  PageLoader,
  Segmented,
  Select,
  useToast,
} from "@/components/ui";
import { api, buildUrl, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatBytes, formatDateTime } from "@/lib/format";
import { useFiles, useStorageUsage } from "@/lib/queries";
import type { FileItem } from "@/lib/types";

type Scope = "private" | "shared";
type SortKey = "name" | "size" | "modified";

const TYPE_ICONS: Record<string, ReactNode> = {
  video: <Film className="h-4 w-4" />,
  audio: <Music className="h-4 w-4" />,
  image: <ImageIcon className="h-4 w-4" />,
  archive: <FileArchive className="h-4 w-4" />,
  document: <FileText className="h-4 w-4" />,
  application: <Package className="h-4 w-4" />,
};

const TYPES = ["video", "audio", "image", "archive", "document", "application", "other"];

export function FilesPage() {
  const toast = useToast();
  const client = useQueryClient();
  const openAdd = useAddDownload();
  const [params, setParams] = useSearchParams();

  const scope: Scope = params.get("scope") === "shared" ? "shared" : "private";
  const path = params.get("path") ?? "";
  const type = params.get("type") ?? "";
  const sort = (params.get("sort") as SortKey | null) ?? "name";
  const order = params.get("order") === "desc" ? "desc" : "asc";

  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next);
  };
  const go = (nextPath: string) => {
    setSearch("");
    setQ("");
    update({ path: nextPath });
  };

  const usage = useStorageUsage();
  // The shared folder belongs to earlier versions; it is shown while it still holds files.
  const hasShared = (usage.data?.shared.used_bytes ?? 0) > 0;
  const listing = useFiles({ scope, path, q, type, sort, order });
  const items = listing.data?.items ?? [];

  const [folderOpen, setFolderOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [target, setTarget] = useState<FileItem | null>(null);

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["files"] });
    void client.invalidateQueries({ queryKey: ["bootstrap"] });
  };

  const createFolder = useMutation({
    mutationFn: () => api.post<{ ok: boolean; name: string }>("/files/folder", { scope, path, name: folderName.trim() }),
    onSuccess: (result) => {
      toast.success(`Created folder ${result.name}.`);
      setFolderOpen(false);
      setFolderName("");
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (item: FileItem) => api.delete<{ ok: boolean }>("/files", { path: item.path, scope }),
    onSuccess: (_result, item) => {
      toast.success(`Deleted ${item.name}.`);
      refresh();
    },
    onError: (error) => toast.error(error),
    onSettled: () => setTarget(null),
  });

  // The files are on this computer: show them where they are.
  const reveal = useMutation({
    mutationFn: (target: string) => api.post<{ ok: boolean }>("/files/reveal", { scope, path: target }),
    onError: (error) => toast.error(error),
  });

  const submitFolder = (event: FormEvent) => {
    event.preventDefault();
    if (folderName.trim()) createFolder.mutate();
  };

  const sortBy = (key: SortKey) => {
    if (sort === key) update({ order: order === "asc" ? "desc" : "" });
    else update({ sort: key === "name" ? "" : key, order: key === "name" ? "" : "desc" });
  };

  const crumbs = path ? path.split("/") : [];
  const rootLabel = scope === "shared" ? "Shared" : "Files";
  const downloadHref = (item: FileItem) => buildUrl("/files/download", { path: item.path, scope });

  const usedHere = scope === "shared" ? usage.data?.shared.used_bytes : usage.data?.private.used_bytes;
  const limit = scope === "private" ? usage.data?.private.limit_bytes ?? 0 : 0;

  const Th = ({ label, sortKey, className }: { label: string; sortKey?: SortKey; className?: string }) => (
    <th scope="col" className={cn("py-2 pr-4 text-left text-2xs font-medium uppercase tracking-wider text-subtle", className)}>
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

  return (
    <>
      <PageHeader
        title="Files"
        description={
          usedHere === undefined
            ? "Completed downloads."
            : `${formatBytes(usedHere)} used${limit > 0 ? ` of ${formatBytes(limit, 0)}` : ""}${scope === "shared" ? " in the shared folder" : ""}.`
        }
        actions={
          <>
            <Button variant="secondary" icon={<FolderOpen className="h-4 w-4" />} loading={reveal.isPending && reveal.variables === path} onClick={() => reveal.mutate(path)}>
              Open folder
            </Button>
            <Button variant="secondary" icon={<FolderPlus className="h-4 w-4" />} onClick={() => setFolderOpen(true)}>
              New folder
            </Button>
            {scope === "private" && (
              <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => openAdd({ folder: path })}>
                Download here
              </Button>
            )}
          </>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {(hasShared || scope === "shared") && (
          <Segmented
            label="Storage area"
            value={scope}
            onChange={(value) => {
              setSearch("");
              setQ("");
              setParams(value === "shared" ? new URLSearchParams({ scope: "shared" }) : new URLSearchParams());
            }}
            options={[
              { value: "private", label: "Files" },
              { value: "shared", label: "Shared" },
            ]}
          />
        )}
        <div className="relative min-w-[12rem] flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <Input
            type="search"
            aria-label="Search files"
            placeholder={path ? "Search this folder" : "Search files"}
            className="pl-8"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <Select aria-label="Filter by type" value={type} onChange={(event) => update({ type: event.target.value })}>
          <option value="">All types</option>
          {TYPES.map((value) => (
            <option key={value} value={value}>
              {value[0].toUpperCase() + value.slice(1)}
            </option>
          ))}
        </Select>
      </div>

      <nav aria-label="Folder path" className="mb-3 flex flex-wrap items-center gap-1 text-[13px]">
        <button type="button" className={cn("rounded px-1.5 py-0.5 hover:bg-raised", crumbs.length ? "text-muted" : "font-medium text-fg")} onClick={() => go("")}>
          {rootLabel}
        </button>
        {crumbs.map((part, index) => {
          const target = crumbs.slice(0, index + 1).join("/");
          const last = index === crumbs.length - 1;
          return (
            <span key={target} className="flex items-center gap-1">
              <ChevronRight className="h-3.5 w-3.5 text-subtle" />
              <button type="button" className={cn("rounded px-1.5 py-0.5 hover:bg-raised", last ? "font-medium text-fg" : "text-muted")} onClick={() => go(target)}>
                {part}
              </button>
            </span>
          );
        })}
      </nav>

      <Card className="overflow-hidden">
        {listing.isPending ? (
          <PageLoader />
        ) : listing.isError ? (
          <div className="p-4">
            <ErrorNote>{errorMessage(listing.error)}</ErrorNote>
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Folder className="h-5 w-5" />}
            title={q || type ? "No files match" : "This folder is empty"}
            description={q || type ? "Try a different search or type." : "Completed downloads saved here will appear in this list."}
          />
        ) : (
          <table className="w-full table-fixed border-collapse">
            <thead className="bg-raised/40">
              <tr>
                <Th label="Name" sortKey="name" className="pl-4" />
                <Th label="Size" sortKey="size" className="hidden w-24 text-right sm:table-cell" />
                <Th label="Type" className="hidden w-28 md:table-cell" />
                <Th label="Source" className="hidden w-32 lg:table-cell" />
                <Th label="Modified" sortKey="modified" className="hidden w-40 md:table-cell" />
                <th scope="col" className="w-28 py-2 pr-3">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.path} className="border-t border-line transition-colors hover:bg-raised/60">
                  <td className="max-w-0 py-2 pl-4 pr-4">
                    <div className="flex items-center gap-2.5">
                      <span className={cn("shrink-0", item.is_dir ? "text-accent" : "text-subtle")}>
                        {item.is_dir ? <Folder className="h-4 w-4" /> : TYPE_ICONS[item.type] ?? <FileIcon className="h-4 w-4" />}
                      </span>
                      <div className="min-w-0">
                        {item.is_dir ? (
                          <button type="button" className="block max-w-full truncate text-left text-[13px] font-medium text-fg hover:underline" onClick={() => go(item.path)}>
                            {item.name}
                          </button>
                        ) : (
                          <a href={downloadHref(item)} download={item.name} className="block truncate text-[13px] text-fg hover:underline" title={item.name}>
                            {item.name}
                          </a>
                        )}
                        {q && item.path !== item.name && <p className="truncate text-2xs text-subtle">{item.path}</p>}
                        <p className="tabular text-2xs text-subtle sm:hidden">{item.is_dir ? "Folder" : formatBytes(item.size)}</p>
                      </div>
                    </div>
                  </td>
                  <td className="tabular hidden py-2 pr-4 text-right text-xs text-muted sm:table-cell">{formatBytes(item.size)}</td>
                  <td className="hidden py-2 pr-4 text-xs capitalize text-muted md:table-cell">{item.is_dir ? "Folder" : item.type}</td>
                  <td className="hidden truncate py-2 pr-4 text-xs text-muted lg:table-cell" title={item.source_url || undefined}>
                    {item.provider ? providerLabel(item.provider) : "—"}
                  </td>
                  <td className="tabular hidden py-2 pr-4 text-xs text-subtle md:table-cell">{formatDateTime(item.downloaded_at ?? item.modified_at)}</td>
                  <td className="py-2 pr-3">
                    <div className="flex items-center justify-end gap-0.5">
                      <Button variant="ghost" size="icon-sm" aria-label={`Show ${item.name} in its folder`} title="Show in folder" onClick={() => reveal.mutate(item.path)}>
                        <FolderOpen className="h-3.5 w-3.5" />
                      </Button>
                      {!item.is_dir && (
                        <a
                          href={downloadHref(item)}
                          download={item.name}
                          aria-label={`Save a copy of ${item.name}`}
                          title="Save a copy somewhere else"
                          className="inline-flex h-7 w-7 items-center justify-center rounded text-muted transition-colors hover:bg-raised hover:text-fg"
                        >
                          <DownloadIcon className="h-3.5 w-3.5" />
                        </a>
                      )}
                      <Button variant="ghost" size="icon-sm" aria-label={`Delete ${item.name}`} title="Delete" onClick={() => setTarget(item)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {listing.data && listing.data.total > items.length && (
          <p className="border-t border-line px-4 py-2.5 text-xs text-subtle">
            Showing {items.length} of {listing.data.total} entries. Use search to narrow the list.
          </p>
        )}
      </Card>

      <Dialog
        open={folderOpen}
        onClose={() => setFolderOpen(false)}
        title="New folder"
        description={`Inside ${[rootLabel, ...crumbs].join(" / ")}`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setFolderOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" form="new-folder-form" loading={createFolder.isPending} disabled={!folderName.trim()}>
              Create folder
            </Button>
          </>
        }
      >
        <form id="new-folder-form" onSubmit={submitFolder} className="space-y-3">
          <Field label="Folder name">
            {(props) => <Input {...props} value={folderName} maxLength={200} onChange={(event) => setFolderName(event.target.value)} />}
          </Field>
          {createFolder.isError && <ErrorNote>{errorMessage(createFolder.error)}</ErrorNote>}
        </form>
      </Dialog>

      <ConfirmDialog
        open={target !== null}
        onClose={() => setTarget(null)}
        title={target?.is_dir ? "Delete this folder?" : "Delete this file?"}
        message={
          <>
            <span className="break-all font-medium text-fg">{target?.name}</span>
            {target?.is_dir ? " and everything inside it" : ""} will be permanently deleted from the server.
          </>
        }
        confirmLabel="Delete"
        destructive
        loading={remove.isPending}
        onConfirm={() => target && remove.mutate(target)}
      />
    </>
  );
}
