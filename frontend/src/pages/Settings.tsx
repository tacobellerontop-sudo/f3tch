import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, CircleMinus, RefreshCw, Search } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Navigate, NavLink, useParams } from "react-router-dom";

import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorNote,
  Field,
  Input,
  Notice,
  PageHeader,
  PageLoader,
  Segmented,
  Select,
  Switch,
  useToast,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatBytes, formatDuration, PRIORITIES } from "@/lib/format";
import { useHealth, useLogs, useSettings, useUpdateStatus } from "@/lib/queries";
import { useNetwork } from "@/lib/netspeed";
import { useTheme } from "@/lib/theme";
import type { AppSettings, SettingsPayload, ThemeChoice } from "@/lib/types";

import { UpdatesTab } from "./Updates";

const TABS = [
  { id: "general", label: "General" },
  { id: "downloads", label: "Downloads" },
  { id: "storage", label: "Storage" },
  { id: "advanced", label: "Advanced" },
  { id: "updates", label: "Updates" },
  { id: "diagnostics", label: "Diagnostics" },
  { id: "logs", label: "Logs" },
] as const;

type TabId = (typeof TABS)[number]["id"];

const RESTART_LABELS: Record<string, string> = {
  "network.port": "Port",
  "storage.storage_root": "Storage location",
  "downloads.use_aria2": "aria2 engine",
  "downloads.aria2_path": "aria2 path",
};

function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <Card className="p-5">
      <h2 className="text-[13px] font-semibold text-fg">{title}</h2>
      {description && <p className="mt-1 text-xs leading-5 text-muted">{description}</p>}
      <div className="mt-4 space-y-4">{children}</div>
    </Card>
  );
}

// -- appearance ---------------------------------------------------------------

function AppearanceSection() {
  const toast = useToast();
  const client = useQueryClient();
  const { choice, setChoice } = useTheme();

  const save = useMutation({
    mutationFn: (theme: ThemeChoice) => api.patch<{ theme: ThemeChoice }>("/preferences", { theme }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["bootstrap"] }),
    onError: (error) => toast.error(error),
  });

  return (
    <Section title="Appearance">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[13px] font-medium text-fg">Theme</p>
          <p className="text-xs text-subtle">System follows the setting of this computer.</p>
        </div>
        <Segmented<ThemeChoice>
          label="Theme"
          value={choice}
          onChange={(value) => {
            setChoice(value);
            save.mutate(value);
          }}
          options={[
            { value: "dark", label: "Dark" },
            { value: "light", label: "Light" },
            { value: "system", label: "System" },
          ]}
        />
      </div>
    </Section>
  );
}

// -- settings forms -----------------------------------------------------------

type SectionKey = keyof AppSettings;

interface FormApi<K extends SectionKey> {
  values: AppSettings[K];
  set: <F extends keyof AppSettings[K]>(field: F, value: AppSettings[K][F]) => void;
}

function SettingsForm<K extends SectionKey>({
  section,
  payload,
  title,
  description,
  children,
}: {
  section: K;
  payload: SettingsPayload;
  title: string;
  description?: ReactNode;
  children: (form: FormApi<K>) => ReactNode;
}) {
  const toast = useToast();
  const client = useQueryClient();
  const saved = payload.settings[section];
  const [values, setValues] = useState<AppSettings[K]>(saved);
  const savedText = JSON.stringify(saved);

  // Follow changes made elsewhere (another window).
  useEffect(() => {
    setValues(JSON.parse(savedText) as AppSettings[K]);
  }, [savedText]);

  const dirty = JSON.stringify(values) !== savedText;
  const save = useMutation({
    mutationFn: () => api.put<SettingsPayload>("/settings", { [section]: values }),
    onSuccess: (result) => {
      client.setQueryData(["settings", "values"], result);
      void client.invalidateQueries({ queryKey: ["settings"] });
      void client.invalidateQueries({ queryKey: ["bootstrap"] });
      void client.invalidateQueries({ queryKey: ["files"] });
      const restart = (result.changed ?? []).some((name) => name in RESTART_LABELS);
      toast.success(restart ? "Saved. Some changes apply after FETCH restarts." : "Settings saved.");
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  const form: FormApi<K> = {
    values,
    set: (field, value) => {
      save.reset();
      setValues((current) => ({ ...current, [field]: value }));
    },
  };

  return (
    <form onSubmit={submit}>
      <Section title={title} description={description}>
        {children(form)}
        {save.isError && <ErrorNote>{errorMessage(save.error)}</ErrorNote>}
        <div className="flex items-center gap-2 border-t border-line pt-4">
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!dirty}>
            Save changes
          </Button>
          {dirty && (
            <Button type="button" variant="ghost" onClick={() => setValues(JSON.parse(savedText) as AppSettings[K])}>
              Discard
            </Button>
          )}
        </div>
      </Section>
    </form>
  );
}

function Num({ label, hint, value, onChange, min = 0, max, step = 1 }: { label: string; hint?: ReactNode; value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number }) {
  return (
    <Field label={label} hint={hint}>
      {(props) => (
        <Input
          {...props}
          type="number"
          inputMode="decimal"
          min={min}
          max={max}
          step={step}
          required
          value={Number.isFinite(value) ? value : ""}
          onChange={(event) => onChange(event.target.value === "" ? Number.NaN : Number(event.target.value))}
        />
      )}
    </Field>
  );
}

function Text({ label, hint, value, onChange, placeholder, maxLength }: { label: string; hint?: ReactNode; value: string; onChange: (value: string) => void; placeholder?: string; maxLength?: number }) {
  return (
    <Field label={label} hint={hint}>
      {(props) => <Input {...props} value={value} placeholder={placeholder} maxLength={maxLength} spellCheck={false} onChange={(event) => onChange(event.target.value)} />}
    </Field>
  );
}

const grid = "grid gap-4 sm:grid-cols-2";

function GeneralTab({ payload }: { payload: SettingsPayload }) {
  return (
    <SettingsForm section="general" payload={payload} title="General">
      {({ values, set }) => (
        <>
          <div className={grid}>
            <Text label="Application name" hint="Shown in the sidebar and the window title." value={values.app_name} maxLength={40} onChange={(value) => set("app_name", value)} />
            <Field label="Language" hint="English is the only language included at the moment.">
              {(props) => (
                <Select {...props} className="w-full" value={values.language} onChange={(event) => set("language", event.target.value)}>
                  <option value="en">English</option>
                </Select>
              )}
            </Field>
          </div>
          <div className="space-y-4 border-t border-line pt-4">
            <Switch
              label="Keep running when the window is closed"
              description="Downloads continue in the background. FETCH stays reachable from its icon near the clock, where it can also be quit."
              checked={values.close_to_tray}
              onChange={(value) => set("close_to_tray", value)}
            />
          </div>
        </>
      )}
    </SettingsForm>
  );
}

function DownloadsTab({ payload }: { payload: SettingsPayload }) {
  return (
    <SettingsForm section="downloads" payload={payload} title="Downloads" description="Queue limits, retries and the transfer engine.">
      {({ values, set }) => (
        <>
          <div className={grid}>
            <Num label="Simultaneous downloads" hint="The rest wait in the queue." min={1} max={32} value={values.max_global_concurrent} onChange={(value) => set("max_global_concurrent", value)} />
            <Num label="Speed limit (KB/s)" hint="For all downloads together. 0 means no limit. Applies to downloads that start from now on." value={values.speed_limit_kbps} onChange={(value) => set("speed_limit_kbps", value)} />
            <Num label="Automatic retries" hint="For temporary errors only. 0 turns retries off." max={20} value={values.retry_count} onChange={(value) => set("retry_count", value)} />
            <Num label="Retry delay (seconds)" hint="Wait before each automatic retry." min={1} max={3600} value={values.retry_delay_seconds} onChange={(value) => set("retry_delay_seconds", value)} />
            <Field label="Default priority">
              {(props) => (
                <Select {...props} className="w-full" value={values.default_priority} onChange={(event) => set("default_priority", Number(event.target.value))}>
                  {PRIORITIES.map((priority) => (
                    <option key={priority.value} value={priority.value}>
                      {priority.label}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          <div className="space-y-4 border-t border-line pt-4">
            <Switch
              label="Use aria2 for large files"
              description="Splits large resumable downloads over several connections. Without it FETCH's built-in engine handles everything. Restart required."
              checked={values.use_aria2}
              onChange={(value) => set("use_aria2", value)}
            />
            <div className={grid}>
              <Text label="aria2 path" hint="Empty: look in tools\aria2 and on PATH. Restart required." value={values.aria2_path} placeholder="C:\Tools\aria2\aria2c.exe" maxLength={500} onChange={(value) => set("aria2_path", value)} />
              <Num label="Use aria2 from (MB)" hint="Smaller files use the built-in engine." min={1} value={values.aria2_min_size_mb} onChange={(value) => set("aria2_min_size_mb", value)} />
              <Num label="Connections per download" min={1} max={16} value={values.aria2_connections} onChange={(value) => set("aria2_connections", value)} />
            </div>
          </div>
        </>
      )}
    </SettingsForm>
  );
}

function StorageTab({ payload }: { payload: SettingsPayload }) {
  const pendingMove = payload.effective.storage_root !== payload.effective.active_storage_root;
  return (
    <SettingsForm section="storage" payload={payload} title="Storage" description={<>Files are currently stored in <code className="font-mono text-fg">{payload.effective.active_storage_root}</code>.</>}>
      {({ values, set }) => (
        <>
          {pendingMove && (
            <Notice tone="warning">
              After the next restart FETCH will use <code className="font-mono">{payload.effective.storage_root}</code>. Existing files are not moved automatically.
            </Notice>
          )}
          <Text
            label="Storage location"
            hint="Absolute folder path. Empty uses the path from the configuration file. Restart required; move existing files yourself while FETCH is stopped."
            value={values.storage_root}
            placeholder={payload.effective.active_storage_root}
            maxLength={500}
            onChange={(value) => set("storage_root", value)}
          />
          <div className={grid}>
            <Num label="Total storage limit (GB)" hint="0 means no limit." step={0.1} value={values.max_storage_gb} onChange={(value) => set("max_storage_gb", value)} />
            <Num label="Clean up temporary files after (hours)" hint="Leftovers of removed downloads." min={1} value={values.temp_cleanup_hours} onChange={(value) => set("temp_cleanup_hours", value)} />
            <Num label="Delete completed files after (days)" hint="0 keeps files until you delete them." max={3650} value={values.auto_delete_days} onChange={(value) => set("auto_delete_days", value)} />
            <Num label="Keep history for (days)" hint="Finished entries and activity. 0 keeps everything." max={3650} value={values.history_retention_days} onChange={(value) => set("history_retention_days", value)} />
          </div>
        </>
      )}
    </SettingsForm>
  );
}

function AdvancedTab({ payload }: { payload: SettingsPayload }) {
  const { port } = payload.effective;
  return (
    <SettingsForm
      section="network"
      payload={payload}
      title="Network"
      description={<>FETCH runs on this computer only and is not reachable from other devices. The window talks to it at <code className="font-mono text-fg">127.0.0.1:{port}</code>.</>}
    >
      {({ values, set }) => (
        <>
          <Switch
            label="Download over every network connection at once"
            description="With two Wi-Fi adapters, or Wi-Fi plus Ethernet or a phone, FETCH splits each large download over all of them. Each one needs its own way to the internet."
            checked={values.multi_path}
            onChange={(value) => set("multi_path", value)}
          />
          {values.multi_path && <AdapterChoice chosen={values.adapters} onChange={(value) => set("adapters", value)} />}
          <div className={grid}>
            <Num label="Port" hint="Change it only if another program uses the same port. 0 uses the default. Restart required." max={65535} value={values.port} onChange={(value) => set("port", value)} />
          </div>
        </>
      )}
    </SettingsForm>
  );
}

function AdapterChoice({ chosen, onChange }: { chosen: string[]; onChange: (value: string[]) => void }) {
  const network = useNetwork();
  const adapters = network.data?.adapters ?? [];
  if (network.isPending) return <p className="text-xs text-subtle">Looking for network connections…</p>;
  if (adapters.length === 0) return <Notice tone="warning">FETCH found no network connection with an address.</Notice>;
  const all = chosen.length === 0;
  const toggle = (id: string, on: boolean) => {
    const current = all ? adapters.map((adapter) => adapter.id) : chosen;
    const next = on ? [...new Set([...current, id])] : current.filter((item) => item !== id);
    onChange(next.length === adapters.length ? [] : next);
  };
  return (
    <fieldset className="rounded-md border border-line p-3">
      <legend className="px-1 text-xs text-muted">Connections to use</legend>
      <ul className="space-y-2">
        {adapters.map((adapter) => (
          <li key={adapter.id}>
            <label className="flex cursor-pointer items-center gap-2.5 text-[13px] text-fg">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-[rgb(var(--accent))]"
                checked={all || chosen.includes(adapter.id)}
                onChange={(event) => toggle(adapter.id, event.target.checked)}
              />
              <span className="min-w-0 flex-1 truncate">{adapter.name}</span>
              <span className="tabular text-2xs text-subtle">{adapter.address}</span>
              {adapter.online === false && <Badge tone="warning">No internet</Badge>}
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}

// -- diagnostics ----------------------------------------------------------------

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="text-xs text-subtle">{label}</dt>
      <dd className="tabular min-w-0 truncate text-right text-[13px] text-fg">{children}</dd>
    </div>
  );
}

function DiagnosticsTab() {
  const toast = useToast();
  const client = useQueryClient();
  const health = useHealth();
  const [confirmRestart, setConfirmRestart] = useState(false);
  const [cleanupResult, setCleanupResult] = useState<Record<string, unknown> | null>(null);

  const restart = useMutation({
    mutationFn: () => api.post<{ ok: boolean; requeued: number }>("/admin/workers/restart"),
    onSuccess: (result) => {
      toast.success(`Workers restarted. ${result.requeued} download${result.requeued === 1 ? "" : "s"} requeued.`);
      void client.invalidateQueries({ queryKey: ["downloads"] });
      void health.refetch();
    },
    onError: (error) => toast.error(error),
    onSettled: () => setConfirmRestart(false),
  });
  const cleanup = useMutation({
    mutationFn: () => api.post<Record<string, unknown>>("/admin/cleanup"),
    onSuccess: (result) => {
      setCleanupResult(result);
      void client.invalidateQueries({ queryKey: ["files"] });
    },
    onError: (error) => toast.error(error),
  });

  if (health.isPending) return <Card><PageLoader /></Card>;
  if (health.isError) return <ErrorNote>{errorMessage(health.error)}</ErrorNote>;
  const data = health.data;

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <h2 className="text-[13px] font-semibold text-fg">System health</h2>
            <Badge tone={data.status === "ok" ? "success" : "warning"} dot>
              {data.status === "ok" ? "All systems normal" : "Needs attention"}
            </Badge>
          </div>
          <Button size="sm" variant="ghost" icon={<RefreshCw className={cn("h-3.5 w-3.5", health.isFetching && "animate-spin")} />} onClick={() => void health.refetch()}>
            Refresh
          </Button>
        </div>
        <ul className="mt-4 divide-y divide-line">
          {data.checks.map((check) => (
            <li key={check.name} className="flex items-start gap-3 py-2.5">
              {check.ok ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
              ) : check.optional ? (
                <CircleMinus className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
              ) : (
                <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
              )}
              <div className="min-w-0">
                <p className="text-[13px] text-fg">
                  {check.name}
                  {!check.ok && check.optional && <span className="ml-2 text-xs text-subtle">optional, not in use</span>}
                </p>
                <p className="break-all text-xs text-subtle">{check.detail}</p>
              </div>
            </li>
          ))}
        </ul>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="text-[13px] font-semibold text-fg">Server</h2>
          <dl className="mt-3 divide-y divide-line">
            <Fact label="Version">{data.version}</Fact>
            <Fact label="Uptime">{formatDuration(data.uptime_seconds)}</Fact>
            <Fact label="Listening on">{data.listening.bind_address}:{data.listening.port}</Fact>
            <Fact label="Python">{data.python}</Fact>
            <Fact label="Platform"><span title={data.platform}>{data.platform}</span></Fact>
            <Fact label="Live connections">{data.realtime_clients}</Fact>
            <Fact label="Blocked internal-address requests">{data.guard_proxy_denied}</Fact>
          </dl>
        </Card>
        <Card className="p-5">
          <h2 className="text-[13px] font-semibold text-fg">Downloads and storage</h2>
          <dl className="mt-3 divide-y divide-line">
            <Fact label="Transferring now">{data.manager.transferring}</Fact>
            <Fact label="Active jobs">{data.manager.active_jobs}</Fact>
            <Fact label="Combined speed">{data.manager.aggregate_speed_bps > 0 ? `${formatBytes(data.manager.aggregate_speed_bps)}/s` : "Idle"}</Fact>
            <Fact label="Completed">{data.downloads.completed ?? 0}</Fact>
            <Fact label="Failed">{data.downloads.failed ?? 0}</Fact>
            <Fact label="Used by FETCH">{formatBytes(data.storage_used_bytes)}</Fact>
            <Fact label="Free on disk">{formatBytes(data.disk.free)} of {formatBytes(data.disk.total, 0)}</Fact>
          </dl>
        </Card>
      </div>

      <Section title="Maintenance">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 max-w-xl">
            <p className="text-[13px] font-medium text-fg">Restart download workers</p>
            <p className="text-xs leading-5 text-subtle">Stops every running transfer and queues it again. Partial data is kept, so resumable downloads continue where they stopped.</p>
          </div>
          <Button variant="secondary" onClick={() => setConfirmRestart(true)}>Restart workers</Button>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-3 border-t border-line pt-4">
          <div className="min-w-0 max-w-xl">
            <p className="text-[13px] font-medium text-fg">Clean up now</p>
            <p className="text-xs leading-5 text-subtle">Removes leftover temporary files and applies the retention rules from the Storage tab. Runs automatically every hour.</p>
          </div>
          <Button variant="secondary" loading={cleanup.isPending} onClick={() => cleanup.mutate()}>Run cleanup</Button>
        </div>
        {cleanupResult && (
          <Notice tone="success">
            Cleanup finished.{" "}
            {Object.entries(cleanupResult)
              .filter(([, value]) => typeof value === "number")
              .map(([key, value]) => `${key.replace(/_/g, " ")}: ${String(value)}`)
              .join(", ")}
          </Notice>
        )}
      </Section>

      <ConfirmDialog
        open={confirmRestart}
        onClose={() => setConfirmRestart(false)}
        title="Restart download workers?"
        message="Running downloads are interrupted and queued again."
        confirmLabel="Restart workers"
        loading={restart.isPending}
        onConfirm={() => restart.mutate()}
      />
    </div>
  );
}

// -- logs -------------------------------------------------------------------------

const LEVEL_STYLE: Record<string, string> = { ERROR: "text-danger", CRITICAL: "text-danger", WARNING: "text-warning", INFO: "text-muted", DEBUG: "text-subtle" };
const HIDDEN_KEYS = new Set(["ts", "level", "logger", "message"]);

function LogsTab() {
  const [level, setLevel] = useState("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  const logs = useLogs({ level, q, limit: 300 });
  const items = logs.data?.items ?? [];

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
        <div className="relative min-w-[12rem] flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <Input type="search" aria-label="Search logs" placeholder="Search logs" className="pl-8" value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
        <Select aria-label="Minimum level" value={level} onChange={(event) => setLevel(event.target.value)}>
          <option value="">All levels</option>
          <option value="INFO">Info and above</option>
          <option value="WARNING">Warnings and errors</option>
          <option value="ERROR">Errors only</option>
        </Select>
        <Button size="sm" variant="ghost" className="ml-auto" icon={<RefreshCw className={cn("h-3.5 w-3.5", logs.isFetching && "animate-spin")} />} onClick={() => void logs.refetch()}>
          Refresh
        </Button>
      </div>
      {logs.isPending ? (
        <PageLoader />
      ) : logs.isError ? (
        <div className="p-4"><ErrorNote>{errorMessage(logs.error)}</ErrorNote></div>
      ) : items.length === 0 ? (
        <EmptyState title="No log entries match" />
      ) : (
        <ol className="max-h-[32rem] overflow-auto py-1 font-mono text-[11px] leading-5">
          {items.map((entry, index) => {
            const extra = Object.entries(entry).filter(([key]) => !HIDDEN_KEYS.has(key));
            return (
              <li key={`${entry.ts}-${index}`} className="flex gap-3 px-4 hover:bg-raised/60">
                <time className="shrink-0 text-subtle">{String(entry.ts).replace("T", " ").slice(0, 19)}</time>
                <span className={cn("w-14 shrink-0", LEVEL_STYLE[entry.level] ?? "text-muted")}>{entry.level}</span>
                <span className="min-w-0 break-words text-fg">
                  {entry.message}
                  {extra.length > 0 && <span className="text-subtle"> {extra.map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`).join(" ")}</span>}
                </span>
              </li>
            );
          })}
        </ol>
      )}
      {logs.data && <p className="border-t border-line px-4 py-2 text-2xs text-subtle">Newest {items.length} entries from {logs.data.file}. Secrets are redacted before they are written.</p>}
    </Card>
  );
}

// -- page -------------------------------------------------------------------------

export function SettingsPage() {
  const params = useParams();
  const tab = (params.tab ?? "general") as TabId;
  const settings = useSettings();
  const tabs = TABS;
  const updateAvailable = useUpdateStatus().data?.update_available ?? false;

  if (!tabs.some((item) => item.id === tab)) return <Navigate to="/settings" replace />;
  const payload = settings.data;
  const restart = payload?.restart_required ?? [];
  const needsSettings = !["updates", "diagnostics", "logs"].includes(tab);

  return (
    <>
      <PageHeader title="Settings" description="How FETCH looks and works on this computer." />
      {restart.length > 0 && (
        <Notice tone="warning" className="mb-4">
          Restart FETCH to apply: {restart.map((name) => RESTART_LABELS[name] ?? name).join(", ")}. Quit FETCH from its icon near the clock, then start it again.
        </Notice>
      )}
      <div className="grid gap-6 lg:grid-cols-[180px_minmax(0,1fr)]">
        {tabs.length > 1 && (
          <nav aria-label="Settings sections" className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible">
            {tabs.map((item) => (
              <NavLink
                key={item.id}
                to={item.id === "general" ? "/settings" : `/settings/${item.id}`}
                end
                className={({ isActive }) =>
                  cn(
                    "whitespace-nowrap rounded px-2.5 py-1.5 text-[13px] transition-colors",
                    isActive ? "bg-raised font-medium text-fg" : "text-muted hover:bg-raised/60 hover:text-fg",
                  )
                }
              >
                {item.label}
                {item.id === "updates" && updateAvailable && (
                  <span className="ml-2 inline-block h-1.5 w-1.5 rounded-full bg-accent align-middle" aria-label="Update available" />
                )}
              </NavLink>
            ))}
          </nav>
        )}
        <div className={cn("min-w-0 space-y-4", "")}>
          {tab === "general" && <AppearanceSection />}
          {tab === "diagnostics" && <DiagnosticsTab />}
          {tab === "updates" && <UpdatesTab />}
          {tab === "logs" && <LogsTab />}
          {needsSettings &&
            (settings.isPending ? (
              <Card><PageLoader /></Card>
            ) : settings.isError || !payload ? (
              <ErrorNote>{errorMessage(settings.error)}</ErrorNote>
            ) : (
              <>
                {tab === "general" && <GeneralTab payload={payload} />}
                {tab === "downloads" && <DownloadsTab payload={payload} />}
                {tab === "storage" && <StorageTab payload={payload} />}
                {tab === "advanced" && <AdvancedTab payload={payload} />}
              </>
            ))}
        </div>
      </div>
    </>
  );
}
