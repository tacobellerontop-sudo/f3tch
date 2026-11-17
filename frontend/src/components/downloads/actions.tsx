import { Copy, FolderOpen, MoreHorizontal, Pause, Play, RotateCw, Trash2, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button, Checkbox, ConfirmDialog, Menu, useToast, type MenuItem } from "@/components/ui";
import { useDownloadAction, useRemoveDownload } from "@/lib/queries";
import type { Download, DownloadAction, DownloadState } from "@/lib/types";

const CAN: Record<DownloadAction, DownloadState[]> = {
  pause: ["pending", "queued", "waiting", "inspecting", "downloading"],
  resume: ["paused"],
  cancel: ["pending", "queued", "waiting", "inspecting", "downloading", "paused", "verifying"],
  retry: ["failed", "cancelled"],
};

export function canDo(action: DownloadAction, state: DownloadState): boolean {
  return CAN[action].includes(state);
}

export function filesLink(download: Download, isOwner: boolean): string {
  const folder = download.final_path.includes("/") ? download.final_path.slice(0, download.final_path.lastIndexOf("/")) : "";
  const params = new URLSearchParams({ scope: download.destination });
  if (folder) params.set("path", folder);
  if (download.destination === "private" && !isOwner) params.set("owner", String(download.user_id));
  return `/files?${params.toString()}`;
}

interface Props {
  download: Download;
  currentUserId: number;
  /** Show the primary action as an inline icon button. */
  inline?: boolean;
}

export function DownloadActions({ download, currentUserId, inline = true }: Props) {
  const toast = useToast();
  const navigate = useNavigate();
  const action = useDownloadAction();
  const remove = useRemoveDownload();
  const [confirm, setConfirm] = useState<"cancel" | "remove" | null>(null);
  const [deleteFile, setDeleteFile] = useState(false);

  const run = (name: DownloadAction) =>
    action.mutate({ id: download.id, action: name }, { onError: (error) => toast.error(error) });

  const copyUrl = async () => {
    try {
      await navigator.clipboard.writeText(download.url);
      toast.success("Download URL copied.");
    } catch {
      toast.error("The browser did not allow copying. Copy it from the details panel.");
    }
  };

  const items: MenuItem[] = [];
  if (canDo("pause", download.state)) items.push({ label: "Pause", icon: <Pause />, onSelect: () => run("pause") });
  if (canDo("resume", download.state)) items.push({ label: "Resume", icon: <Play />, onSelect: () => run("resume") });
  if (canDo("retry", download.state)) items.push({ label: "Retry", icon: <RotateCw />, onSelect: () => run("retry") });
  if (canDo("cancel", download.state)) items.push({ label: "Cancel", icon: <X />, onSelect: () => setConfirm("cancel") });
  if (download.state === "completed" && download.final_path) {
    items.push({
      label: "Open containing folder",
      icon: <FolderOpen />,
      onSelect: () => navigate(filesLink(download, download.user_id === currentUserId)),
    });
  }
  items.push({ label: "Copy download URL", icon: <Copy />, onSelect: () => void copyUrl(), separatorBefore: items.length > 0 });
  items.push({
    label: "Remove from history",
    icon: <Trash2 />,
    danger: true,
    separatorBefore: true,
    onSelect: () => {
      setDeleteFile(false);
      setConfirm("remove");
    },
  });

  const primary: { action: DownloadAction; label: string; icon: JSX.Element } | null = canDo("pause", download.state)
    ? { action: "pause", label: "Pause", icon: <Pause className="h-3.5 w-3.5" /> }
    : canDo("resume", download.state)
      ? { action: "resume", label: "Resume", icon: <Play className="h-3.5 w-3.5" /> }
      : canDo("retry", download.state)
        ? { action: "retry", label: "Retry", icon: <RotateCw className="h-3.5 w-3.5" /> }
        : null;

  return (
    <div className="flex items-center justify-end gap-0.5" onClick={(event) => event.stopPropagation()}>
      {inline && primary && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`${primary.label} ${download.filename || "download"}`}
          title={primary.label}
          disabled={action.isPending}
          onClick={() => run(primary.action)}
        >
          {primary.icon}
        </Button>
      )}
      <Menu
        items={items}
        trigger={(props) => (
          <Button variant="ghost" size="icon-sm" aria-label={`More actions for ${download.filename || "download"}`} {...props}>
            <MoreHorizontal className="h-4 w-4" />
          </Button>
        )}
      />
      <ConfirmDialog
        open={confirm === "cancel"}
        onClose={() => setConfirm(null)}
        title="Cancel this download?"
        message={
          <>
            <span className="font-medium text-fg">{download.filename || download.url}</span> will stop and the data
            downloaded so far will be deleted.
          </>
        }
        confirmLabel="Cancel download"
        destructive
        loading={action.isPending}
        onConfirm={() =>
          action.mutate(
            { id: download.id, action: "cancel" },
            { onError: (error) => toast.error(error), onSettled: () => setConfirm(null) },
          )
        }
      />
      <ConfirmDialog
        open={confirm === "remove"}
        onClose={() => setConfirm(null)}
        title="Remove from history?"
        message={
          <>
            <span className="font-medium text-fg">{download.filename || download.url}</span> will be removed from the
            list{canDo("cancel", download.state) ? " and stopped" : ""}.
          </>
        }
        confirmLabel="Remove"
        destructive
        loading={remove.isPending}
        onConfirm={() =>
          remove.mutate(
            { id: download.id, deleteFile },
            {
              onSuccess: () => toast.success("Removed from history."),
              onError: (error) => toast.error(error),
              onSettled: () => setConfirm(null),
            },
          )
        }
      >
        {download.state === "completed" && (
          <Checkbox
            label="Also delete the downloaded file"
            checked={deleteFile}
            onChange={(event) => setDeleteFile(event.target.checked)}
          />
        )}
      </ConfirmDialog>
    </div>
  );
}
