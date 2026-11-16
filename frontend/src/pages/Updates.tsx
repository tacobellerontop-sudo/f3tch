import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, DownloadCloud, ExternalLink, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Badge, Button, Card, ConfirmDialog, ErrorNote, Notice, PageLoader, Spinner, Switch, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useSettings, useUpdateStatus } from "@/lib/queries";
import type { SettingsPayload, UpdateStatus } from "@/lib/types";
import { clearUpdating, markUpdating, UPDATE_TIMEOUT_MS, type UpdatingState } from "@/lib/updating";

/** Settings > Updates: version, release notes and the install action. */
export function UpdatesTab() {
  const toast = useToast();
  const client = useQueryClient();
  const status = useUpdateStatus();
  const settings = useSettings();
  const [confirm, setConfirm] = useState(false);

  const check = useMutation({
    mutationFn: () => api.get<UpdateStatus>("/admin/update", { refresh: true }),
    onSuccess: (data) => {
      client.setQueryData(["settings", "update"], data);
      if (data.error) toast.error(data.error);
      else toast.success(data.update_available ? `FETCH ${data.latest_version} is available.` : "FETCH is up to date.");
    },
    onError: (error) => toast.error(error),
  });

  const install = useMutation({
    mutationFn: () => api.post<{ ok: boolean }>("/admin/update/apply"),
    onSuccess: () => {
      setConfirm(false);
      // From here on the server stops; the app switches to the waiting screen.
      markUpdating(status.data?.current_version ?? "");
    },
    onError: (error) => {
      setConfirm(false);
      toast.error(error);
      void status.refetch();
    },
  });

  const autoCheck = useMutation({
    mutationFn: (enabled: boolean) => api.put<SettingsPayload>("/settings", { general: { check_for_updates: enabled } }),
    onSuccess: (result) => {
      client.setQueryData(["settings", "values"], result);
      void client.invalidateQueries({ queryKey: ["settings", "update"] });
    },
    onError: (error) => toast.error(error),
  });

  if (status.isPending) {
    return (
      <Card>
        <PageLoader />
      </Card>
    );
  }
  if (status.isError) return <ErrorNote>{errorMessage(status.error)}</ErrorNote>;

  const data = status.data;
  const run = data.last_run;
  const checking = check.isPending || status.isFetching;
  const autoCheckOn = settings.data?.settings.general.check_for_updates ?? data.auto_check;

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-[15px] font-semibold text-fg">FETCH {data.current_version}</h2>
              {data.update_available ? (
                <Badge tone="accent" dot>
                  Version {data.latest_version} is available
                </Badge>
              ) : data.latest_version && !data.error ? (
                <Badge tone="success" dot>
                  Up to date
                </Badge>
              ) : null}
            </div>
            <p className="mt-1 text-xs leading-5 text-muted">
              {data.checked_at ? `Last checked ${formatRelative(data.checked_at)}.` : "Not checked yet."}
              {data.repository ? ` Releases come from github.com/${data.repository}.` : ""}
            </p>
          </div>
          <Button
            variant="secondary"
            icon={<RefreshCw className={checking ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />}
            disabled={checking || data.in_progress}
            onClick={() => check.mutate()}
          >
            Check now
          </Button>
        </div>

        {data.error && (
          <Notice tone="warning" className="mt-4">
            {data.error}
          </Notice>
        )}

        {data.in_progress && (
          <Notice className="mt-4">
            <span className="flex items-center gap-2">
              <Spinner className="h-3.5 w-3.5" /> An update is running{run?.message ? `: ${run.message}` : "."}
            </span>
          </Notice>
        )}

        {data.update_available && (
          <div className="mt-5 border-t border-line pt-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-[13px] font-semibold text-fg">{data.release_name || `FETCH ${data.latest_version}`}</h3>
              <span className="flex items-center gap-3 text-xs text-subtle">
                {data.published_at && <span>Published {formatDateTime(data.published_at)}</span>}
                {data.release_url && (
                  <a href={data.release_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-accent hover:underline">
                    View on GitHub <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </span>
            </div>
            {data.notes ? (
              <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-canvas p-3 font-sans text-xs leading-5 text-muted">
                {data.notes}
              </pre>
            ) : (
              <p className="mt-3 text-xs text-subtle">This release has no notes.</p>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Button
                variant="primary"
                icon={<DownloadCloud className="h-4 w-4" />}
                disabled={!data.can_apply || data.in_progress || (data.source === "release" && !data.package_available)}
                onClick={() => setConfirm(true)}
              >
                Install update
              </Button>
              <span className="text-xs text-subtle">Takes a few minutes. A backup of the database is made first.</span>
            </div>
            {!data.can_apply && (
              <Notice tone="warning" className="mt-3">
                {data.apply_blocker} To update by hand, run <code className="font-mono">.\update.ps1</code> in the FETCH folder.
              </Notice>
            )}
            {data.can_apply && data.source === "release" && !data.package_available && (
              <Notice tone="warning" className="mt-3">
                This release has no installable package attached, so it cannot be installed from here.
              </Notice>
            )}
            {data.can_apply && data.source === "git" && (
              <p className="mt-3 text-xs leading-5 text-subtle">
                This installation is a git checkout: the update runs <code className="font-mono">git pull --ff-only</code> on the current branch.
              </p>
            )}
          </div>
        )}
      </Card>

      {run && !data.in_progress && run.state !== "running" && (
        <Card className="flex items-start gap-3 p-5">
          {run.state === "failed" ? (
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
          ) : (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
          )}
          <div className="min-w-0">
            <h2 className="text-[13px] font-semibold text-fg">{run.state === "failed" ? "The last update failed" : "Last update"}</h2>
            <p className="mt-1 break-words text-xs leading-5 text-muted">{run.message}</p>
            <p className="mt-1 text-2xs text-subtle">
              {run.finished_at ? formatDateTime(run.finished_at) : ""}
              {run.state === "failed" ? " · Details are in logs\\update.log. FETCH keeps running on the previous version where possible." : ""}
            </p>
          </div>
        </Card>
      )}

      <Card className="p-5">
        <Switch
          label="Check for updates automatically"
          description="A few times a day FETCH asks GitHub for the latest release. The request is anonymous and sends nothing about this server. Updates are never installed without your click."
          checked={autoCheckOn}
          disabled={autoCheck.isPending || !data.repository}
          onChange={(enabled) => autoCheck.mutate(enabled)}
        />
      </Card>

      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title={`Install FETCH ${data.latest_version ?? ""}?`}
        message={
          <>
            FETCH stops for a few minutes and everyone is disconnected. Running downloads are saved and continue afterwards.
            The database and configuration are backed up first, and the package is verified against its published checksum.
          </>
        }
        confirmLabel="Install update"
        loading={install.isPending}
        onConfirm={() => install.mutate()}
      />
    </div>
  );
}

type Phase = "stopping" | "installing" | "done" | "timeout";

/** Full-screen wait while update.ps1 replaces and restarts the server. */
export function UpdatingScreen({ state }: { state: UpdatingState }) {
  const [phase, setPhase] = useState<Phase>("stopping");
  const [version, setVersion] = useState("");
  const wentDown = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const finish = (found: string) => {
      setVersion(found);
      setPhase("done");
      timer = window.setTimeout(() => {
        clearUpdating();
        window.location.assign("/settings/updates");
      }, 2500);
    };

    const poll = async () => {
      if (cancelled) return;
      if (Date.now() - state.startedAt > UPDATE_TIMEOUT_MS) {
        setPhase("timeout");
        return;
      }
      try {
        const response = await fetch("/api/health", { cache: "no-store" });
        if (!response.ok) throw new Error("not ready");
        const body = (await response.json()) as { version?: string };
        const found = body.version ?? "";
        // Finished when the server answers with another version, or answers
        // again after having been away (same version: nothing newer, or a failure).
        if (!cancelled && (wentDown.current || (found && found !== state.fromVersion))) {
          finish(found);
          return;
        }
      } catch {
        if (!wentDown.current) {
          wentDown.current = true;
          if (!cancelled) setPhase("installing");
        }
      }
      if (!cancelled) timer = window.setTimeout(() => void poll(), 3000);
    };

    timer = window.setTimeout(() => void poll(), 2000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [state.startedAt, state.fromVersion]);

  const leave = () => {
    clearUpdating();
    window.location.assign("/settings/updates");
  };

  return (
    <div className="flex h-screen items-center justify-center p-6" role="status" aria-live="polite">
      <div className="w-full max-w-sm text-center">
        <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-lg border border-line bg-raised">
          {phase === "done" ? (
            <CheckCircle2 className="h-5 w-5 text-success" />
          ) : phase === "timeout" ? (
            <CircleAlert className="h-5 w-5 text-warning" />
          ) : (
            <Spinner className="h-5 w-5" />
          )}
        </div>
        <h1 className="text-[15px] font-semibold text-fg">
          {phase === "done" ? "FETCH is back" : phase === "timeout" ? "This is taking longer than expected" : "Updating FETCH"}
        </h1>
        <p className="mt-2 text-[13px] leading-5 text-muted">
          {phase === "stopping" && "Saving running downloads and stopping the server."}
          {phase === "installing" && "Installing the new version. The page reconnects by itself; you can leave it open."}
          {phase === "done" && (version ? `Now running version ${version}. Reloading.` : "Reloading.")}
          {phase === "timeout" &&
            "The server has not come back yet. On the host computer, look at logs\\update.log and run .\\start.ps1 if FETCH is not running."}
        </p>
        {phase === "timeout" && (
          <Button variant="secondary" className="mt-4" onClick={leave}>
            Back to FETCH
          </Button>
        )}
      </div>
    </div>
  );
}
