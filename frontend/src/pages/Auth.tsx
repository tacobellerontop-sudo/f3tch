import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowDownToLine } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { Button, ErrorNote, Field, Input, Notice, PageLoader } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Me } from "@/lib/types";

function AuthLayout({ title, subtitle, children, footer }: { title: string; subtitle: ReactNode; children: ReactNode; footer?: ReactNode }) {
  const { bootstrap } = useAuth();
  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-[380px]">
        <div className="mb-7 flex flex-col items-center text-center">
          <span className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg border border-line bg-surface text-accent">
            <ArrowDownToLine className="h-5 w-5" strokeWidth={2.4} />
          </span>
          <h1 className="text-lg font-semibold text-fg">{title}</h1>
          <p className="mt-1 text-[13px] text-muted">{subtitle}</p>
        </div>
        <div className="rounded-lg border border-line bg-surface p-5">{children}</div>
        {footer && <div className="mt-4 text-center text-xs text-subtle">{footer}</div>}
        <p className="mt-6 text-center text-2xs text-subtle">
          {bootstrap?.app_name ?? "FETCH"} {bootstrap ? `v${bootstrap.version}` : ""} · self-hosted
        </p>
      </div>
    </div>
  );
}

function passwordHint(min: number, mixed: boolean): string {
  return `At least ${min} characters${mixed ? ", with letters and digits" : ""}.`;
}

export function LoginPage() {
  const { bootstrap, signIn } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => api.post<Me>("/auth/login", { username, password }),
    onSuccess: signIn,
  });
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate();
  };
  return (
    <AuthLayout
      title={`Sign in to ${bootstrap?.app_name ?? "FETCH"}`}
      subtitle="Use the account you were invited with."
      footer={
        bootstrap?.invitations_required ? (
          "No account? Ask the owner of this server for an invitation link."
        ) : (
          <>
            No account? <Link to="/register" className="text-accent hover:underline">Create one</Link>
          </>
        )
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <Field label="Username">
          {(props) => (
            <Input {...props} autoFocus autoComplete="username" autoCapitalize="none" spellCheck={false} required value={username} onChange={(e) => setUsername(e.target.value)} />
          )}
        </Field>
        <Field label="Password">
          {(props) => <Input {...props} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}
        </Field>
        <ErrorNote>{login.isError ? errorMessage(login.error) : null}</ErrorNote>
        <Button type="submit" variant="primary" className="w-full" loading={login.isPending}>
          Sign in
        </Button>
      </form>
    </AuthLayout>
  );
}

function tokenFromAddress(): string {
  const match = /^#setup=([A-Za-z0-9_-]{8,200})$/.exec(window.location.hash);
  return match ? match[1] : "";
}

export function SetupPage() {
  const { bootstrap, signIn } = useAuth();
  // FETCH.exe opens the dashboard with the token in the address fragment
  // (which browsers never send to a server), so it does not have to be typed.
  const [form, setForm] = useState(() => ({ setup_token: tokenFromAddress(), username: "", password: "", confirm: "" }));
  const setup = useMutation({
    mutationFn: () => api.post<Me>("/setup", { setup_token: form.setup_token, username: form.username, password: form.password }),
    onSuccess: signIn,
  });
  const mismatch = form.confirm !== "" && form.confirm !== form.password;
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!mismatch) setup.mutate();
  };
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: event.target.value }));
  return (
    <AuthLayout title="Welcome to FETCH" subtitle="Create the administrator account for this server.">
      <form onSubmit={onSubmit} className="space-y-4">
        <Notice>
          The setup token is shown in the FETCH window (or by <span className="font-mono">install.ps1</span>) each time
          FETCH starts until setup is done. It is also stored in <span className="font-mono">config/fetch.toml</span>.
        </Notice>
        <Field label="Setup token">
          {(props) => <Input {...props} autoFocus required spellCheck={false} autoComplete="off" className="font-mono" value={form.setup_token} onChange={set("setup_token")} />}
        </Field>
        <Field label="Administrator username" hint="3–32 characters: letters, digits, dot, dash, underscore.">
          {(props) => <Input {...props} required autoComplete="username" autoCapitalize="none" spellCheck={false} pattern="[A-Za-z0-9][A-Za-z0-9._\-]{2,31}" value={form.username} onChange={set("username")} />}
        </Field>
        <Field label="Password" hint={passwordHint(bootstrap?.password_min_length ?? 10, bootstrap?.password_require_mixed ?? false)}>
          {(props) => <Input {...props} type="password" required autoComplete="new-password" value={form.password} onChange={set("password")} />}
        </Field>
        <Field label="Repeat password" error={mismatch ? "The passwords do not match." : null}>
          {(props) => <Input {...props} type="password" required autoComplete="new-password" value={form.confirm} onChange={set("confirm")} />}
        </Field>
        <ErrorNote>{setup.isError ? errorMessage(setup.error) : null}</ErrorNote>
        <Button type="submit" variant="primary" className="w-full" loading={setup.isPending} disabled={mismatch}>
          Create administrator
        </Button>
      </form>
    </AuthLayout>
  );
}

export function InvitePage() {
  const { token = "" } = useParams();
  const { bootstrap, signIn } = useAuth();
  const [form, setForm] = useState({ username: "", display_name: "", password: "", confirm: "" });
  const invitation = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => api.get<{ valid: boolean; role: string }>(`/auth/invitation/${encodeURIComponent(token)}`),
    enabled: token !== "",
    retry: false,
  });
  const register = useMutation({
    mutationFn: () => api.post<Me>("/auth/register", { token, username: form.username, display_name: form.display_name, password: form.password }),
    onSuccess: signIn,
  });
  const mismatch = form.confirm !== "" && form.confirm !== form.password;
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: event.target.value }));
  const signInLink = <Link to="/login" className="text-accent hover:underline">Sign in instead</Link>;

  if (token === "" && bootstrap?.invitations_required) {
    return (
      <AuthLayout title="Invitation required" subtitle="This server only accepts invited members." footer={signInLink}>
        <p className="text-[13px] text-muted">Ask the owner of this server for an invitation link, then open it in your browser.</p>
      </AuthLayout>
    );
  }
  if (token !== "" && invitation.isPending) {
    return (
      <AuthLayout title="Checking invitation" subtitle="One moment…">
        <PageLoader />
      </AuthLayout>
    );
  }
  if (token !== "" && invitation.isError) {
    return (
      <AuthLayout title="Invitation not valid" subtitle="This link cannot be used." footer={signInLink}>
        <ErrorNote>{errorMessage(invitation.error)}</ErrorNote>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout
      title={`Join ${bootstrap?.app_name ?? "FETCH"}`}
      subtitle={token ? "You have been invited. Choose your sign-in details." : "Create your account."}
      footer={signInLink}
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (!mismatch) register.mutate();
        }}
      >
        <Field label="Username" hint="3–32 characters: letters, digits, dot, dash, underscore.">
          {(props) => <Input {...props} autoFocus required autoComplete="username" autoCapitalize="none" spellCheck={false} pattern="[A-Za-z0-9][A-Za-z0-9._\-]{2,31}" value={form.username} onChange={set("username")} />}
        </Field>
        <Field label="Display name (optional)">
          {(props) => <Input {...props} maxLength={128} value={form.display_name} onChange={set("display_name")} />}
        </Field>
        <Field label="Password" hint={passwordHint(bootstrap?.password_min_length ?? 10, bootstrap?.password_require_mixed ?? false)}>
          {(props) => <Input {...props} type="password" required autoComplete="new-password" value={form.password} onChange={set("password")} />}
        </Field>
        <Field label="Repeat password" error={mismatch ? "The passwords do not match." : null}>
          {(props) => <Input {...props} type="password" required autoComplete="new-password" value={form.confirm} onChange={set("confirm")} />}
        </Field>
        <ErrorNote>{register.isError ? errorMessage(register.error) : null}</ErrorNote>
        <Button type="submit" variant="primary" className="w-full" loading={register.isPending} disabled={mismatch}>
          Create account
        </Button>
      </form>
    </AuthLayout>
  );
}
