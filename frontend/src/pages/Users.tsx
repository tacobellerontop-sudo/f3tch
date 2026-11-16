import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy, Gauge, KeyRound, Link2, MonitorSmartphone, MoreHorizontal, ShieldCheck, ShieldOff, Trash2, UserCheck, UserPlus, UserX } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import {
  Badge,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorNote,
  Field,
  Input,
  Menu,
  Notice,
  PageHeader,
  PageLoader,
  Segmented,
  Select,
  useToast,
  type MenuItem,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useMe } from "@/lib/auth";
import { formatBytes, formatDateTime, formatRelative, GB } from "@/lib/format";
import { useAllSessions, useInvitations, useUsers } from "@/lib/queries";
import type { AdminUser, CreatedInvitation, Invitation, QuotaLimits, Role, SessionInfo } from "@/lib/types";

type Tab = "users" | "invitations" | "sessions";

function useRefreshUsers() {
  const client = useQueryClient();
  return () => void client.invalidateQueries({ queryKey: ["users"] });
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** A read-only value with a copy button; the text stays selectable as a fallback. */
function CopyField({ label, value }: { label: string; value: string }) {
  const toast = useToast();
  return (
    <Field label={label}>
      {(props) => (
        <div className="flex gap-2">
          <Input {...props} readOnly value={value} className="font-mono text-xs" onFocus={(event) => event.target.select()} />
          <Button
            variant="secondary"
            icon={<Copy className="h-3.5 w-3.5" />}
            onClick={async () => {
              if (await copyText(value)) toast.success("Copied.");
              else toast.error("The browser did not allow copying. Select the text and copy it manually.");
            }}
          >
            Copy
          </Button>
        </div>
      )}
    </Field>
  );
}

// -- quota ------------------------------------------------------------------

const round = (value: number) => Math.round(value * 100) / 100;

function QuotaDialog({ user, onClose }: { user: AdminUser | null; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefreshUsers();
  const [form, setForm] = useState({ concurrent: "0", storage: "0", file: "0", daily: "0", bandwidth: "0", queued: "0" });

  useEffect(() => {
    if (!user) return;
    setForm({
      concurrent: String(user.quota.max_concurrent),
      storage: String(round(user.quota.max_storage_bytes / GB)),
      file: String(round(user.quota.max_file_bytes / GB)),
      daily: String(user.quota.daily_downloads),
      bandwidth: String(Math.round(user.quota.bandwidth_bps / 1024)),
      queued: String(user.quota.max_queued),
    });
  }, [user]);

  const number = (text: string) => Math.max(0, Number(text) || 0);
  const save = useMutation({
    mutationFn: () => {
      const body: QuotaLimits = {
        max_concurrent: Math.round(number(form.concurrent)),
        max_storage_bytes: Math.round(number(form.storage) * GB),
        max_file_bytes: Math.round(number(form.file) * GB),
        daily_downloads: Math.round(number(form.daily)),
        bandwidth_bps: Math.round(number(form.bandwidth) * 1024),
        max_queued: Math.round(number(form.queued)),
      };
      return api.put<QuotaLimits>(`/users/${user!.id}/quota`, body);
    },
    onSuccess: () => {
      toast.success(`Quota updated for ${user!.username}.`);
      refresh();
      onClose();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  const field = (key: keyof typeof form, label: string, hint: string, step = "1", max?: number) => (
    <Field label={label} hint={hint}>
      {(props) => (
        <Input {...props} type="number" inputMode="decimal" min={0} max={max} step={step} value={form[key]} onChange={(event) => setForm({ ...form, [key]: event.target.value })} />
      )}
    </Field>
  );

  return (
    <Dialog
      open={user !== null}
      onClose={onClose}
      title={`Quota for ${user?.username ?? ""}`}
      description="Zero means no limit. Changes apply to new downloads immediately."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="quota-form" loading={save.isPending}>
            Save quota
          </Button>
        </>
      }
    >
      <form id="quota-form" onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        {field("concurrent", "Simultaneous downloads", "Running at the same time.", "1", 32)}
        {field("queued", "Queued downloads", "Unfinished downloads allowed at once.")}
        {field("storage", "Storage (GB)", user ? `Currently using ${formatBytes(user.storage_bytes)}.` : "", "0.01")}
        {field("file", "Largest file (GB)", "Bigger files are rejected.", "0.01")}
        {field("daily", "Downloads per 24 hours", "New downloads in a rolling day.")}
        {field("bandwidth", "Speed limit (KB/s)", "Shared by this user's downloads.")}
        {save.isError && <ErrorNote className="sm:col-span-2">{errorMessage(save.error)}</ErrorNote>}
      </form>
    </Dialog>
  );
}

// -- users ------------------------------------------------------------------

function UsersTab() {
  const me = useMe();
  const toast = useToast();
  const refresh = useRefreshUsers();
  const users = useUsers();
  const [quotaFor, setQuotaFor] = useState<number | null>(null);
  const [resetFor, setResetFor] = useState<AdminUser | null>(null);
  const [deleteFor, setDeleteFor] = useState<AdminUser | null>(null);
  const [deleteFiles, setDeleteFiles] = useState(false);
  const [temporary, setTemporary] = useState<{ username: string; password: string } | null>(null);

  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { role?: Role; is_active?: boolean } }) => api.patch<AdminUser>(`/users/${id}`, body),
    onSuccess: () => {
      toast.success("User updated.");
      refresh();
    },
    onError: (error) => toast.error(error),
  });
  const reset = useMutation({
    mutationFn: (user: AdminUser) => api.post<{ temporary_password: string | null }>(`/users/${user.id}/reset-password`, {}),
    onSuccess: (result, user) => {
      if (result.temporary_password) setTemporary({ username: user.username, password: result.temporary_password });
      refresh();
    },
    onError: (error) => toast.error(error),
    onSettled: () => setResetFor(null),
  });
  const remove = useMutation({
    mutationFn: (user: AdminUser) => api.delete<{ ok: boolean }>(`/users/${user.id}`, { delete_files: deleteFiles }),
    onSuccess: (_result, user) => {
      toast.success(`Deleted ${user.username}.`);
      refresh();
    },
    onError: (error) => toast.error(error),
    onSettled: () => setDeleteFor(null),
  });

  if (users.isPending) return <Card><PageLoader /></Card>;
  if (users.isError) return <ErrorNote>{errorMessage(users.error)}</ErrorNote>;

  const menu = (user: AdminUser): MenuItem[] => {
    const self = user.id === me.user.id;
    return [
      { label: "Edit quota", icon: <Gauge />, onSelect: () => setQuotaFor(user.id) },
      { label: "Reset password", icon: <KeyRound />, onSelect: () => setResetFor(user) },
      user.role === "admin"
        ? { label: "Make regular user", icon: <ShieldOff />, disabled: self, separatorBefore: true, onSelect: () => patch.mutate({ id: user.id, body: { role: "user" } }) }
        : { label: "Make administrator", icon: <ShieldCheck />, separatorBefore: true, onSelect: () => patch.mutate({ id: user.id, body: { role: "admin" } }) },
      user.is_active
        ? { label: "Disable account", icon: <UserX />, disabled: self, onSelect: () => patch.mutate({ id: user.id, body: { is_active: false } }) }
        : { label: "Enable account", icon: <UserCheck />, onSelect: () => patch.mutate({ id: user.id, body: { is_active: true } }) },
      {
        label: "Delete user",
        icon: <Trash2 />,
        danger: true,
        disabled: self,
        separatorBefore: true,
        onSelect: () => {
          setDeleteFiles(false);
          setDeleteFor(user);
        },
      },
    ];
  };

  const th = "py-2 pr-4 text-left text-2xs font-medium uppercase tracking-wider text-subtle";
  return (
    <>
      <Card>
        <table className="w-full table-fixed border-collapse">
          <thead className="bg-raised/40">
            <tr>
              <th scope="col" className={`${th} pl-4`}>User</th>
              <th scope="col" className={`${th} hidden w-28 sm:table-cell`}>Role</th>
              <th scope="col" className={`${th} hidden w-40 md:table-cell`}>Storage</th>
              <th scope="col" className={`${th} hidden w-28 lg:table-cell`}>Downloads</th>
              <th scope="col" className={`${th} hidden w-32 lg:table-cell`}>Last sign-in</th>
              <th scope="col" className="w-14 py-2 pr-3"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {users.data.map((user) => (
              <tr key={user.id} className="border-t border-line">
                <td className="max-w-0 py-2.5 pl-4 pr-4">
                  <p className="truncate text-[13px] font-medium text-fg">
                    {user.username}
                    {user.id === me.user.id && <span className="ml-2 text-xs font-normal text-subtle">you</span>}
                  </p>
                  <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-subtle">
                    {user.display_name && user.display_name !== user.username && <span className="truncate">{user.display_name}</span>}
                    {!user.is_active && <Badge tone="danger">Disabled</Badge>}
                    {user.must_change_password && <Badge tone="warning">Must change password</Badge>}
                    <span className="sm:hidden">{user.role === "admin" ? "Administrator" : "User"}</span>
                  </div>
                </td>
                <td className="hidden py-2.5 pr-4 sm:table-cell">
                  <Badge tone={user.role === "admin" ? "accent" : "neutral"}>{user.role === "admin" ? "Administrator" : "User"}</Badge>
                </td>
                <td className="tabular hidden py-2.5 pr-4 text-xs text-muted md:table-cell">
                  {formatBytes(user.storage_bytes)}
                  <span className="text-subtle">{user.quota.max_storage_bytes > 0 ? ` / ${formatBytes(user.quota.max_storage_bytes, 0)}` : " · no limit"}</span>
                </td>
                <td className="tabular hidden py-2.5 pr-4 text-xs text-muted lg:table-cell">
                  {user.open_downloads} open<span className="text-subtle"> · {user.total_downloads} total</span>
                </td>
                <td className="tabular hidden py-2.5 pr-4 text-xs text-subtle lg:table-cell">{user.last_login_at ? formatRelative(user.last_login_at) : "Never"}</td>
                <td className="py-2.5 pr-3 text-right">
                  <Menu
                    items={menu(user)}
                    trigger={(props) => (
                      <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${user.username}`} {...props}>
                        <MoreHorizontal className="h-4 w-4" />
                      </Button>
                    )}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <QuotaDialog user={users.data.find((user) => user.id === quotaFor) ?? null} onClose={() => setQuotaFor(null)} />

      <ConfirmDialog
        open={resetFor !== null}
        onClose={() => setResetFor(null)}
        title="Reset this password?"
        message={
          <>
            <span className="font-medium text-fg">{resetFor?.username}</span> is signed out everywhere and gets a temporary
            password, shown to you once. They must choose a new one at their next sign-in.
          </>
        }
        confirmLabel="Reset password"
        loading={reset.isPending}
        onConfirm={() => resetFor && reset.mutate(resetFor)}
      />

      <Dialog
        open={temporary !== null}
        onClose={() => setTemporary(null)}
        title="Temporary password"
        description={`Give this to ${temporary?.username ?? "the user"} over a channel you trust. It is not shown again.`}
        size="sm"
        footer={<Button variant="primary" onClick={() => setTemporary(null)}>Done</Button>}
      >
        {temporary && <CopyField label="Password" value={temporary.password} />}
      </Dialog>

      <ConfirmDialog
        open={deleteFor !== null}
        onClose={() => setDeleteFor(null)}
        title="Delete this user?"
        message={
          <>
            <span className="font-medium text-fg">{deleteFor?.username}</span>, their sessions and their download history
            are removed. Running downloads are cancelled. This cannot be undone.
          </>
        }
        confirmLabel="Delete user"
        destructive
        loading={remove.isPending}
        onConfirm={() => deleteFor && remove.mutate(deleteFor)}
      >
        <Checkbox
          label={`Also delete their files (${formatBytes(deleteFor?.storage_bytes ?? 0)})`}
          checked={deleteFiles}
          onChange={(event) => setDeleteFiles(event.target.checked)}
        />
      </ConfirmDialog>
    </>
  );
}

// -- invitations --------------------------------------------------------------

const INVITE_TONE: Record<Invitation["status"], "success" | "neutral" | "danger" | "warning"> = {
  active: "success",
  used: "neutral",
  revoked: "danger",
  expired: "warning",
};

function InvitationsTab({ createOpen, onCloseCreate }: { createOpen: boolean; onCloseCreate: () => void }) {
  const toast = useToast();
  const refresh = useRefreshUsers();
  const invitations = useInvitations();
  const [role, setRole] = useState<Role>("user");
  const [note, setNote] = useState("");
  const [hours, setHours] = useState("");
  const [created, setCreated] = useState<CreatedInvitation | null>(null);
  const [revokeFor, setRevokeFor] = useState<Invitation | null>(null);

  const create = useMutation({
    mutationFn: () => api.post<CreatedInvitation>("/invitations", { role, note: note.trim(), expires_hours: hours ? Number(hours) : null }),
    onSuccess: (result) => {
      setCreated(result);
      setNote("");
      setHours("");
      setRole("user");
      refresh();
    },
  });
  const revoke = useMutation({
    mutationFn: (invitation: Invitation) => api.delete<{ ok: boolean }>(`/invitations/${invitation.id}`),
    onSuccess: () => {
      toast.success("Invitation revoked.");
      refresh();
    },
    onError: (error) => toast.error(error),
    onSettled: () => setRevokeFor(null),
  });

  const close = () => {
    setCreated(null);
    create.reset();
    onCloseCreate();
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate();
  };
  // The server builds the link from its public URL; fall back to this browser's origin.
  const link = created ? (created.url && /^https?:/i.test(created.url) ? created.url : `${window.location.origin}${created.path}`) : "";

  const th = "py-2 pr-4 text-left text-2xs font-medium uppercase tracking-wider text-subtle";
  return (
    <>
      <Card>
        {invitations.isPending ? (
          <PageLoader />
        ) : invitations.isError ? (
          <div className="p-4"><ErrorNote>{errorMessage(invitations.error)}</ErrorNote></div>
        ) : invitations.data.length === 0 ? (
          <EmptyState icon={<Link2 className="h-5 w-5" />} title="No invitations yet" description="Create an invitation link and send it to a friend. Each link works once." />
        ) : (
          <table className="w-full table-fixed border-collapse">
            <thead className="bg-raised/40">
              <tr>
                <th scope="col" className={`${th} pl-4`}>Note</th>
                <th scope="col" className={`${th} w-28`}>Status</th>
                <th scope="col" className={`${th} hidden w-28 sm:table-cell`}>Role</th>
                <th scope="col" className={`${th} hidden w-44 md:table-cell`}>Expires</th>
                <th scope="col" className="w-24 py-2 pr-3"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {invitations.data.map((invitation) => (
                <tr key={invitation.id} className="border-t border-line">
                  <td className="max-w-0 py-2.5 pl-4 pr-4">
                    <p className="truncate text-[13px] text-fg">{invitation.note || <span className="text-subtle">No note</span>}</p>
                    <p className="text-2xs text-subtle">Created {formatRelative(invitation.created_at)}</p>
                  </td>
                  <td className="py-2.5 pr-4"><Badge tone={INVITE_TONE[invitation.status]} className="capitalize">{invitation.status}</Badge></td>
                  <td className="hidden py-2.5 pr-4 text-xs text-muted sm:table-cell">{invitation.role === "admin" ? "Administrator" : "User"}</td>
                  <td className="tabular hidden py-2.5 pr-4 text-xs text-subtle md:table-cell">
                    {invitation.used_at ? `Used ${formatDateTime(invitation.used_at)}` : formatDateTime(invitation.expires_at)}
                  </td>
                  <td className="py-2.5 pr-3 text-right">
                    {invitation.status === "active" && (
                      <Button size="sm" variant="ghost" onClick={() => setRevokeFor(invitation)}>Revoke</Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Dialog
        open={createOpen}
        onClose={close}
        title={created ? "Invitation link" : "Invite someone"}
        description={created ? "Copy the link now. For security it cannot be shown again." : "The link lets one person create an account."}
        footer={
          created ? (
            <Button variant="primary" onClick={close}>Done</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={close}>Cancel</Button>
              <Button variant="primary" type="submit" form="invite-form" loading={create.isPending}>Create link</Button>
            </>
          )
        }
      >
        {created ? (
          <div className="space-y-3">
            <CopyField label="Link" value={link} />
            <Notice>
              Valid until {formatDateTime(created.expires_at)}. The person opening it must be able to reach this server, for
              example over your Tailscale network.
            </Notice>
          </div>
        ) : (
          <form id="invite-form" onSubmit={submit} className="space-y-4">
            <Field label="Note (optional)" hint="Only you see this, to remember who the link was for.">
              {(props) => <Input {...props} value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} />}
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Role">
                {(props) => (
                  <Select {...props} className="w-full" value={role} onChange={(event) => setRole(event.target.value as Role)}>
                    <option value="user">User</option>
                    <option value="admin">Administrator</option>
                  </Select>
                )}
              </Field>
              <Field label="Valid for (hours)" hint="Empty uses the default from Settings.">
                {(props) => <Input {...props} type="number" min={1} max={2160} value={hours} onChange={(event) => setHours(event.target.value)} />}
              </Field>
            </div>
            {role === "admin" && <Notice tone="warning">Administrators can see every user's downloads and files and change all settings.</Notice>}
            {create.isError && <ErrorNote>{errorMessage(create.error)}</ErrorNote>}
          </form>
        )}
      </Dialog>

      <ConfirmDialog
        open={revokeFor !== null}
        onClose={() => setRevokeFor(null)}
        title="Revoke this invitation?"
        message="The link stops working immediately."
        confirmLabel="Revoke"
        destructive
        loading={revoke.isPending}
        onConfirm={() => revokeFor && revoke.mutate(revokeFor)}
      />
    </>
  );
}

// -- sessions -----------------------------------------------------------------

export function describeAgent(agent: string): string {
  if (!agent) return "Unknown device";
  const browser = /Edg\//.test(agent) ? "Edge" : /Firefox\//.test(agent) ? "Firefox" : /Chrome\//.test(agent) ? "Chrome" : /Safari\//.test(agent) ? "Safari" : "";
  const system = /Windows/.test(agent) ? "Windows" : /Android/.test(agent) ? "Android" : /iPhone|iPad/.test(agent) ? "iOS" : /Mac OS X/.test(agent) ? "macOS" : /Linux/.test(agent) ? "Linux" : "";
  return [browser, system].filter(Boolean).join(" on ") || agent.slice(0, 60);
}

export function SessionTable({ sessions, showUser, onRevoke, busy }: { sessions: SessionInfo[]; showUser: boolean; onRevoke: (session: SessionInfo) => void; busy: boolean }) {
  const th = "py-2 pr-4 text-left text-2xs font-medium uppercase tracking-wider text-subtle";
  return (
    <table className="w-full table-fixed border-collapse">
      <thead className="bg-raised/40">
        <tr>
          <th scope="col" className={`${th} pl-4`}>{showUser ? "User and device" : "Device"}</th>
          <th scope="col" className={`${th} hidden w-36 sm:table-cell`}>Address</th>
          <th scope="col" className={`${th} hidden w-28 md:table-cell`}>Last active</th>
          <th scope="col" className="w-24 py-2 pr-3"><span className="sr-only">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        {sessions.map((session) => (
          <tr key={session.id} className="border-t border-line">
            <td className="max-w-0 py-2.5 pl-4 pr-4">
              <p className="flex items-center gap-2 truncate text-[13px] text-fg">
                {showUser ? <span className="font-medium">{session.username}</span> : describeAgent(session.user_agent)}
                {session.current && <Badge tone="accent">This session</Badge>}
              </p>
              <p className="truncate text-2xs text-subtle" title={session.user_agent}>
                {showUser ? `${describeAgent(session.user_agent)} · ` : ""}Signed in {formatRelative(session.created_at)}
              </p>
            </td>
            <td className="tabular hidden truncate py-2.5 pr-4 text-xs text-muted sm:table-cell">{session.ip_address || "—"}</td>
            <td className="tabular hidden py-2.5 pr-4 text-xs text-subtle md:table-cell">{formatRelative(session.last_seen_at)}</td>
            <td className="py-2.5 pr-3 text-right">
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => onRevoke(session)}>
                {session.current ? "Sign out" : "Revoke"}
              </Button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SessionsTab() {
  const toast = useToast();
  const refresh = useRefreshUsers();
  const sessions = useAllSessions();
  const revoke = useMutation({
    mutationFn: (session: SessionInfo) => api.delete<{ ok: boolean }>(`/admin/sessions/${session.id}`),
    onSuccess: () => {
      toast.success("Session revoked.");
      refresh();
    },
    onError: (error) => toast.error(error),
  });
  return (
    <Card>
      {sessions.isPending ? (
        <PageLoader />
      ) : sessions.isError ? (
        <div className="p-4"><ErrorNote>{errorMessage(sessions.error)}</ErrorNote></div>
      ) : sessions.data.length === 0 ? (
        <EmptyState icon={<MonitorSmartphone className="h-5 w-5" />} title="No active sessions" />
      ) : (
        <SessionTable sessions={sessions.data} showUser busy={revoke.isPending} onRevoke={(session) => revoke.mutate(session)} />
      )}
    </Card>
  );
}

export function UsersPage() {
  const [tab, setTab] = useState<Tab>("users");
  const [inviteOpen, setInviteOpen] = useState(false);
  return (
    <>
      <PageHeader
        title="Users"
        description="Accounts, invitations and signed-in devices."
        actions={
          <Button
            variant="primary"
            icon={<UserPlus className="h-4 w-4" />}
            onClick={() => {
              setTab("invitations");
              setInviteOpen(true);
            }}
          >
            Invite user
          </Button>
        }
      />
      <Segmented
        label="Section"
        className="mb-4"
        value={tab}
        onChange={setTab}
        options={[
          { value: "users", label: "Users" },
          { value: "invitations", label: "Invitations" },
          { value: "sessions", label: "Sessions" },
        ]}
      />
      {tab === "users" && <UsersTab />}
      {tab === "invitations" && <InvitationsTab createOpen={inviteOpen} onCloseCreate={() => setInviteOpen(false)} />}
      {tab === "sessions" && <SessionsTab />}
    </>
  );
}
