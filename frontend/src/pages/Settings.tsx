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
  Textarea,
  useToast,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth, useMe } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatBytes, formatDuration, PRIORITIES } from "@/lib/format";
import { useHealth, useLogs, useMySessions, useSettings } from "@/lib/queries";
import { useTheme } from "@/lib/theme";
import type { AppSettings, Me, SessionInfo, SettingsPayload, ThemeChoice } from "@/lib/types";

import { SessionTable } from "./Users";

const TABS = [
  { id: "account", label: "Account", admin: false },
  { id: "general", label: "General", admin: true },
  { id: "downloads", label: "Downloads", admin: true },
  { id: "storage", label: "Storage", admin: true },
  { id: "network", label: "Network", admin: true },
  { id: "security", label: "Security", admin: true },
  { id: "quotas", label: "Default quotas", admin: true },
  { id: "diagnostics", label: "Diagnostics", admin: true },
  { id: "logs", label: "Logs", admin: true },
] as const;

type TabId = (typeof TABS)[number]["id"];

const RESTART_LABELS: Record<string, string> = {
  "network.bind_address": "Bind address",
  "network.port": "Port",
  "network.cors_origins": "Allowed origins",
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

// -- account ------------------------------------------------------------------

function ProfileSection() {
  const me = useMe();
  const { signIn } = useAuth();
  const toast = useToast();
  const { choice, setChoice } = useTheme();
  const [name, setName] = useState(me.user.display_name);

  const save = useMutation({
    mutationFn: (body: { display_name?: string; theme?: ThemeChoice }) => api.patch<Me>("/auth/me", body),
    onSuccess: (result, body) => {
      signIn(result);
      if (body.display_name !== undefined) toast.success("Profile saved.");
    },
    onError: (error) => toast.error(error),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ display_name: name });
  };

  return (
    <Section title="Profile">
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <Field label="Username" className="w-48">
          {(props) => <Input {...props} value={me.user.username} readOnly disabled />}
        </Field>
        <Field label="Display name" className="min-w-[12rem] flex-1">
          {(props) => <Input {...props} value={name} maxLength={128} onChange={(event) => setName(event.target.value)} />}
        </Field>
        <Button type="submit" variant="secondary" loading={save.isPending} disabled={name.trim() === me.user.display_name}>
          Save
        </Button>
      </form>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
        <div>
          <p className="text-[13px] font-medium text-fg">Theme</p>
          <p className="text-xs text-subtle">Saved to your account and this browser.</p>
        </div>
        <Segmented<ThemeChoice>
          label="Theme"
          value={choice}
          onChange={(value) => {
            setChoice(value);
            save.mutate({ theme: value });
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

function PasswordSection() {
  const me = useMe();
  const { bootstrap, refresh } = useAuth();
  const toast = useToast();
  const client = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const mismatch = repeat.length > 0 && next !== repeat;

  const change = useMutation({
    mutationFn: () => api.post<{ ok: boolean; sessions_revoked: number }>("/auth/password", { current_password: current, new_password: next }),
    onSuccess: (result) => {
      toast.success(
        result.sessions_revoked > 0
          ? `Password changed. ${result.sessions_revoked} other session${result.sessions_revoked === 1 ? " was" : "s were"} signed out.`
          : "Password changed.",
      );
      setCurrent("");
      setNext("");
      setRepeat("");
      void refresh();
      void client.invalidateQueries({ queryKey: ["me", "sessions"] });
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!mismatch) change.mutate();
  };
  const minLength = bootstrap?.password_min_length ?? 10;

  return (
    <Section
      title="Password"
      description={`At least ${minLength} characters${bootstrap?.password_require_mixed ? ", with upper and lower case letters and a digit" : ""}. Changing it signs out your other devices.`}
    >
      {me.user.must_change_password && <Notice tone="warning">An administrator reset your password. Choose a new one now.</Notice>}
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-3">
        <Field label="Current password">
          {(props) => <Input {...props} type="password" autoComplete="current-password" required value={current} onChange={(event) => setCurrent(event.target.value)} />}
        </Field>
        <Field label="New password">
          {(props) => <Input {...props} type="password" autoComplete="new-password" required minLength={minLength} value={next} onChange={(event) => setNext(event.target.value)} />}
        </Field>
        <Field label="Repeat new password" error={mismatch ? "The passwords do not match." : null}>
          {(props) => <Input {...props} type="password" autoComplete="new-password" required value={repeat} onChange={(event) => setRepeat(event.target.value)} />}
        </Field>
        {change.isError && <ErrorNote className="sm:col-span-3">{errorMessage(change.error)}</ErrorNote>}
        <div className="sm:col-span-3">
          <Button type="submit" variant="primary" loading={change.isPending} disabled={!current || !next || !repeat || mismatch}>
            Change password
          </Button>
        </div>
      </form>
    </Section>
  );
}

function MySessionsSection() {
  const { signOut } = useAuth();
  const toast = useToast();
  const client = useQueryClient();
  const sessions = useMySessions();
  const revoke = useMutation({
    mutationFn: (session: SessionInfo) => api.delete<{ ok: boolean }>(`/auth/sessions/${session.id}`),
    onSuccess: () => {
      toast.success("That device was signed out.");
      void client.invalidateQueries({ queryKey: ["me", "sessions"] });
    },
    onError: (error) => toast.error(error),
  });
  return (
    <Card className="overflow-hidden">
      <div className="px-5 pb-3 pt-5">
        <h2 className="text-[13px] font-semibold text-fg">Signed-in devices</h2>
        <p className="mt-1 text-xs text-muted">Revoke any session you do not recognise, then change your password.</p>
      </div>
      {sessions.isPending ? (
        <PageLoader />
      ) : sessions.isError ? (
        <div className="p-4"><ErrorNote>{errorMessage(sessions.error)}</ErrorNote></div>
      ) : (
        <SessionTable
          sessions={sessions.data}
          showUser={false}
          busy={revoke.isPending}
          onRevoke={(session) => (session.current ? void signOut() : revoke.mutate(session))}
        />
      )}
    </Card>
  );
}

// -- admin settings forms -------------------------------------------------------

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

  // Follow changes made elsewhere (another administrator, another tab).
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

function Lines({ label, hint, value, onChange, placeholder }: { label: string; hint?: ReactNode; value: string[]; onChange: (value: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState(value.join("\n"));
  const joined = value.join("\n");
  useEffect(() => {
    setText((current) => (current.split("\n").map((line) => line.trim()).filter(Boolean).join("\n") === joined ? current : joined));
  }, [joined]);
  return (
    <Field label={label} hint={hint}>
      {(props) => (
        <Textarea
          {...props}
          rows={3}
          spellCheck={false}
          placeholder={placeholder}
          className="font-mono text-xs"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            onChange(event.target.value.split("\n").map((line) => line.trim()).filter(Boolean));
          }}
        />
      )}
    </Field>
  );
}

const grid = "grid gap-4 sm:grid-cols-2";

function GeneralTab({ payload }: { payload: SettingsPayload }) {
  return (
    <SettingsForm section="general" payload={payload} title="General">
      {({ values, set }) => (
        <div className={grid}>
          <Text label="Application name" hint="Shown in the sidebar and the browser tab." value={values.app_name} maxLength={40} onChange={(value) => set("app_name", value)} />
          <Field label="Default theme" hint="Used until a person picks their own.">
            {(props) => (
              <Select {...props} className="w-full" value={values.theme} onChange={(event) => set("theme", event.target.value as ThemeChoice)}>
                <option value="dark">Dark</option>
                <option value="light">Light</option>
                <option value="system">Follow the device</option>
              </Select>
            )}
          </Field>
          <Field label="Language" hint="English is the only language included at the moment.">
            {(props) => (
              <Select {...props} className="w-full" value={values.language} onChange={(event) => set("language", event.target.value)}>
                <option value="en">English</option>
              </Select>
            )}
          </Field>
          <Field label="Default destination" hint="Where new downloads are saved unless chosen otherwise.">
            {(props) => (
              <Select {...props} className="w-full" value={values.default_destination} onChange={(event) => set("default_destination", event.target.value as "private" | "shared")}>
                <option value="private">Private folder of the user</option>
                <option value="shared">Shared folder</option>
              </Select>
            )}
          </Field>
        </div>
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
            <Num label="Simultaneous downloads (server)" hint="Across all users." min={1} max={32} value={values.max_global_concurrent} onChange={(value) => set("max_global_concurrent", value)} />
            <Num label="Simultaneous downloads (per user)" hint="A user's own quota can lower this." min={1} max={32} value={values.max_per_user_concurrent} onChange={(value) => set("max_per_user_concurrent", value)} />
            <Num label="Automatic retries" hint="For temporary errors only. 0 turns retries off." max={20} value={values.retry_count} onChange={(value) => set("retry_count", value)} />
            <Num label="Retry delay (seconds)" hint="Doubles after each failed attempt." min={1} max={3600} value={values.retry_delay_seconds} onChange={(value) => set("retry_delay_seconds", value)} />
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
            <Num label="Total storage limit (GB)" hint="For all users together. 0 means no limit." step={0.1} value={values.max_storage_gb} onChange={(value) => set("max_storage_gb", value)} />
            <Num label="Clean up temporary files after (hours)" hint="Leftovers of removed downloads." min={1} value={values.temp_cleanup_hours} onChange={(value) => set("temp_cleanup_hours", value)} />
            <Num label="Delete completed files after (days)" hint="0 keeps files until someone deletes them." max={3650} value={values.auto_delete_days} onChange={(value) => set("auto_delete_days", value)} />
            <Num label="Keep history for (days)" hint="Finished entries and activity. 0 keeps everything." max={3650} value={values.history_retention_days} onChange={(value) => set("history_retention_days", value)} />
          </div>
          <div className="space-y-4 border-t border-line pt-4">
            <Switch label="Shared folder" description="A common area every signed-in user can browse and download from." checked={values.shared_enabled} onChange={(value) => set("shared_enabled", value)} />
            <Switch label="Users may save to the shared folder" description="When off, only administrators can add or delete shared files." checked={values.users_can_write_shared} disabled={!values.shared_enabled} onChange={(value) => set("users_can_write_shared", value)} />
          </div>
        </>
      )}
    </SettingsForm>
  );
}

function NetworkTab({ payload }: { payload: SettingsPayload }) {
  const { bind_address: bind, port } = payload.effective;
  const local = bind === "127.0.0.1" || bind === "::1";
  return (
    <div className="space-y-4">
      <Section title="Remote access" description="How friends reach this server safely.">
        <Notice tone={bind === "0.0.0.0" || bind === "::" ? "warning" : "info"}>
          FETCH is listening on <code className="font-mono">{bind}:{port}</code>.{" "}
          {local
            ? "Only this computer can open it."
            : bind === "0.0.0.0" || bind === "::"
              ? "Every network this computer is connected to can reach it. Make sure the Windows firewall and your router do not expose this port to the internet."
              : "Devices that can reach this address can open it."}
        </Notice>
        <ol className="list-decimal space-y-1.5 pl-5 text-xs leading-5 text-muted">
          <li>Install Tailscale on this computer and on each friend's device, and share this machine with them (or add them to your tailnet).</li>
          <li>
            Keep the bind address at <code className="font-mono text-fg">127.0.0.1</code> and run{" "}
            <code className="font-mono text-fg">tailscale serve --bg {port}</code>. Tailscale then publishes FETCH over HTTPS at this machine's tailnet name, to your tailnet only.
          </li>
          <li>
            Set the public URL below to that address, add <code className="font-mono text-fg">127.0.0.1</code> to the trusted proxies so sign-in limits see real client addresses, and set{" "}
            <code className="font-mono text-fg">cookie_secure = true</code> in the configuration file.
          </li>
          <li>Create an invitation on the Users page and send the link.</li>
        </ol>
        <p className="text-xs leading-5 text-subtle">
          Alternatively bind FETCH to this computer's Tailscale address (100.x.y.z). Do not forward the port on your router or publish it on the open internet.
        </p>
      </Section>
      <SettingsForm section="network" payload={payload} title="Network" description={<>Bootstrap values live in <code className="font-mono text-fg">{payload.effective.config_file}</code>; values set here override them.</>}>
        {({ values, set }) => (
          <>
            <div className={grid}>
              <Text label="Bind address" hint="Empty uses the configuration file. Restart required." value={values.bind_address} placeholder={bind} maxLength={64} onChange={(value) => set("bind_address", value)} />
              <Num label="Port" hint="0 uses the configuration file. Restart required." max={65535} value={values.port} onChange={(value) => set("port", value)} />
            </div>
            <Text label="Public URL" hint="The address friends use, for invitation links. Example: https://fetch.your-tailnet.ts.net" value={values.public_url} maxLength={300} onChange={(value) => set("public_url", value)} />
            <Lines label="Trusted proxies" hint="One IP address or CIDR range per line. Only these may report the client address through X-Forwarded-For." value={values.trusted_proxies} placeholder="127.0.0.1" onChange={(value) => set("trusted_proxies", value)} />
            <Lines label="Allowed cross-origin sites" hint="One origin per line. Leave empty unless another site must call the API. Wildcards are rejected. Restart required." value={values.cors_origins} placeholder="https://example.com" onChange={(value) => set("cors_origins", value)} />
          </>
        )}
      </SettingsForm>
    </div>
  );
}

function SecurityTab({ payload }: { payload: SettingsPayload }) {
  return (
    <SettingsForm section="security" payload={payload} title="Security">
      {({ values, set }) => (
        <>
          <Switch label="Require an invitation to register" description="Recommended. When off, anyone who can reach this server can create an account." checked={values.invitations_required} onChange={(value) => set("invitations_required", value)} />
          {!values.invitations_required && <Notice tone="warning">Open registration is only reasonable on a private network you fully control.</Notice>}
          <div className={grid}>
            <Num label="Invitation validity (hours)" min={1} max={2160} value={values.invitation_expiry_hours} onChange={(value) => set("invitation_expiry_hours", value)} />
            <Num label="Session length (hours)" hint="People are signed out after this long." min={1} max={8760} value={values.session_hours} onChange={(value) => set("session_hours", value)} />
            <Num label="Minimum password length" min={8} max={128} value={values.password_min_length} onChange={(value) => set("password_min_length", value)} />
          </div>
          <Switch label="Require mixed characters" description="Upper and lower case letters and a digit." checked={values.password_require_mixed} onChange={(value) => set("password_require_mixed", value)} />
          <div className="grid gap-4 border-t border-line pt-4 sm:grid-cols-3">
            <Num label="Failed sign-ins allowed" min={2} max={100} value={values.login_max_attempts} onChange={(value) => set("login_max_attempts", value)} />
            <Num label="Within (minutes)" min={1} max={1440} value={values.login_window_minutes} onChange={(value) => set("login_window_minutes", value)} />
            <Num label="Lock out for (minutes)" min={1} max={1440} value={values.login_lockout_minutes} onChange={(value) => set("login_lockout_minutes", value)} />
          </div>
        </>
      )}
    </SettingsForm>
  );
}

function QuotasTab({ payload }: { payload: SettingsPayload }) {
  return (
    <SettingsForm
      section="quota_defaults"
      payload={payload}
      title="Default quotas"
      description="Applied to users created from now on. Existing users keep their limits; change those on the Users page. Zero means no limit."
    >
      {({ values, set }) => (
        <div className={grid}>
          <Num label="Simultaneous downloads" max={32} value={values.max_concurrent} onChange={(value) => set("max_concurrent", value)} />
          <Num label="Queued downloads" value={values.max_queued} onChange={(value) => set("max_queued", value)} />
          <Num label="Storage (GB)" step={0.1} value={values.max_storage_gb} onChange={(value) => set("max_storage_gb", value)} />
          <Num label="Largest file (GB)" step={0.1} value={values.max_file_gb} onChange={(value) => set("max_file_gb", value)} />
          <Num label="Downloads per 24 hours" value={values.daily_downloads} onChange={(value) => set("daily_downloads", value)} />
          <Num label="Speed limit (KB/s)" value={values.bandwidth_kbps} onChange={(value) => set("bandwidth_kbps", value)} />
        </div>
      )}
    </SettingsForm>
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
  const me = useMe();
  const isAdmin = me.user.role === "admin";
  const params = useParams();
  const tab = (params.tab ?? "account") as TabId;
  const settings = useSettings(isAdmin);
  const tabs = TABS.filter((item) => isAdmin || !item.admin);

  if (!tabs.some((item) => item.id === tab)) return <Navigate to="/settings" replace />;
  const payload = settings.data;
  const restart = payload?.restart_required ?? [];
  const needsSettings = !["account", "diagnostics", "logs"].includes(tab);

  return (
    <>
      <PageHeader title="Settings" description={isAdmin ? "Your account and this server." : "Your account."} />
      {isAdmin && restart.length > 0 && (
        <Notice tone="warning" className="mb-4">
          Restart FETCH to apply: {restart.map((name) => RESTART_LABELS[name] ?? name).join(", ")}. Run <code className="font-mono">stop.ps1</code> and then <code className="font-mono">start.ps1</code>.
        </Notice>
      )}
      <div className="grid gap-6 lg:grid-cols-[180px_minmax(0,1fr)]">
        {tabs.length > 1 && (
          <nav aria-label="Settings sections" className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible">
            {tabs.map((item) => (
              <NavLink
                key={item.id}
                to={item.id === "account" ? "/settings" : `/settings/${item.id}`}
                end
                className={({ isActive }) =>
                  cn(
                    "whitespace-nowrap rounded px-2.5 py-1.5 text-[13px] transition-colors",
                    isActive ? "bg-raised font-medium text-fg" : "text-muted hover:bg-raised/60 hover:text-fg",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        )}
        <div className={cn("min-w-0 space-y-4", tabs.length === 1 && "lg:col-span-2 lg:max-w-3xl")}>
          {tab === "account" && (
            <>
              <ProfileSection />
              <PasswordSection />
              <MySessionsSection />
            </>
          )}
          {tab === "diagnostics" && <DiagnosticsTab />}
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
                {tab === "network" && <NetworkTab payload={payload} />}
                {tab === "security" && <SecurityTab payload={payload} />}
                {tab === "quotas" && <QuotasTab payload={payload} />}
              </>
            ))}
        </div>
      </div>
    </>
  );
}
