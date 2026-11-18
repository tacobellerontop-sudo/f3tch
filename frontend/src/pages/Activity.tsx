import { History, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Card, ConfirmDialog, EmptyState, ErrorNote, Input, PageHeader, PageLoader, Pagination, Select, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { useActivity, useClearActivity, useDeleteActivity } from "@/lib/queries";

import { ActivityList } from "./Overview";

const PAGE_SIZE = 50;

const CATEGORIES = [
  { value: "download", label: "Downloads" },
  { value: "file", label: "Files" },
  { value: "provider", label: "Providers" },
  { value: "admin", label: "Settings and maintenance" },
  { value: "system", label: "Starts and stops" },
];

export function ActivityPage() {
  const [category, setCategory] = useState("");
  const [level, setLevel] = useState("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQ(search.trim());
      setPage(1);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const activity = useActivity({ page, page_size: PAGE_SIZE, category, level, q });
  const items = activity.data?.items ?? [];
  const filtered = Boolean(category || level || q);
  const total = activity.data?.total ?? 0;

  const toast = useToast();
  const remove = useDeleteActivity();
  const clear = useClearActivity();
  const [confirmClear, setConfirmClear] = useState(false);

  const deleteOne = (id: number) =>
    remove.mutate(id, {
      onSuccess: () => {
        // Deleting the last entry on a later page: step back so the list is not empty.
        if (items.length === 1 && page > 1) setPage(page - 1);
      },
      onError: (error) => toast.error(error),
    });

  const clearAll = () =>
    clear.mutate(
      { category, level, q },
      {
        onSuccess: (result) => {
          setPage(1);
          toast.success(result.deleted === 1 ? "Deleted 1 activity entry." : `Deleted ${result.deleted} activity entries.`);
        },
        onError: (error) => toast.error(error),
        onSettled: () => setConfirmClear(false),
      },
    );

  return (
    <>
      <PageHeader
        title="Activity"
        description="Everything FETCH did: downloads, file changes and settings."
        actions={
          <Button variant="danger" icon={<Trash2 className="h-4 w-4" />} disabled={total === 0} onClick={() => setConfirmClear(true)}>
            {filtered ? "Delete matching" : "Clear activity"}
          </Button>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[12rem] flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <Input type="search" aria-label="Search activity" placeholder="Search activity" className="pl-8" value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
        <Select
          aria-label="Filter by category"
          value={category}
          onChange={(event) => {
            setCategory(event.target.value);
            setPage(1);
          }}
        >
          <option value="">All categories</option>
          {CATEGORIES.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by level"
          value={level}
          onChange={(event) => {
            setLevel(event.target.value);
            setPage(1);
          }}
        >
          <option value="">All levels</option>
          <option value="info">Info</option>
          <option value="warning">Warnings</option>
          <option value="error">Errors</option>
        </Select>
      </div>

      <Card className="overflow-hidden">
        {activity.isPending ? (
          <PageLoader />
        ) : activity.isError ? (
          <div className="p-4">
            <ErrorNote>{errorMessage(activity.error)}</ErrorNote>
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<History className="h-5 w-5" />}
            title={filtered ? "Nothing matches these filters" : "No activity yet"}
            description={filtered ? undefined : "Downloads, file changes and settings are recorded here."}
          />
        ) : (
          <>
            <ActivityList items={items} onDelete={(item) => deleteOne(item.id)} deleting={remove.isPending ? remove.variables : null} />
            <div className="border-t border-line">
              <Pagination page={page} pageSize={PAGE_SIZE} total={activity.data?.total ?? 0} onPage={setPage} />
            </div>
          </>
        )}
      </Card>

      <ConfirmDialog
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        title={filtered ? "Delete matching activity?" : "Clear all activity?"}
        message={
          filtered
            ? `The ${total === 1 ? "entry" : `${total} entries`} matching these filters will be deleted. Other activity stays.`
            : `All ${total === 1 ? "1 entry" : `${total} entries`} will be deleted.`
        }
        confirmLabel={filtered ? "Delete matching" : "Clear activity"}
        destructive
        loading={clear.isPending}
        onConfirm={clearAll}
      >
        <p>This only removes the activity log. Downloads and files are not touched.</p>
      </ConfirmDialog>
    </>
  );
}
