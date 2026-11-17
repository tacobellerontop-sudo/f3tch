import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, KeyRound, Minus, PlugZap, ScrollText } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorNote,
  Field,
  Input,
  Notice,
  PageHeader,
  PageLoader,
  Spinner,
  Switch,
  Textarea,
  useToast,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useProviders } from "@/lib/queries";
import type { Capabilities, Provider, ProviderError, ProviderTestResult } from "@/lib/types";

const CAPABILITY_LABELS: [keyof Capabilities, string][] = [
  ["inspect", "Link inspection"],
  ["download", "Downloads"],
  ["resume", "Resume"],
  ["anonymous", "Works without an account"],
  ["credentials", "Account or API key"],
  ["checksum", "Integrity check"],
  ["folders", "Folder links"],
];

function statusOf(provider: Provider): { label: string; tone: "success" | "warning" | "neutral" | "danger" } {
  if (!provider.enabled) return { label: "Disabled", tone: "neutral" };
  if (!provider.ready) return { label: "Needs credentials", tone: "warning" };
  if (provider.health === "error") return { label: "Last test failed", tone: "danger" };
  return { label: "Ready", tone: "success" };
}

function CredentialsDialog({ provider, onClose }: { provider: Provider | null; onClose: () => void }) {
  const toast = useToast();
  const client = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>({});
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    setValues({});
  }, [provider?.id]);

  const done = () => {
    void client.invalidateQueries({ queryKey: ["providers"] });
    onClose();
  };
  const save = useMutation({
    mutationFn: () => api.put<Provider>(`/providers/${provider!.id}/credentials`, { values }),
    onSuccess: () => {
      toast.success(`Credentials saved for ${provider!.name}. Run a connection test to confirm them.`);
      done();
    },
  });
  const clear = useMutation({
    mutationFn: () => api.delete<Provider>(`/providers/${provider!.id}/credentials`),
    onSuccess: () => {
      toast.success(`Credentials removed for ${provider!.name}.`);
      setConfirmClear(false);
      done();
    },
    onError: (error) => toast.error(error),
  });

  const fields = provider?.credential_fields ?? [];
  const stored = new Set(provider?.credential_fields_set ?? []);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };

  return (
    <>
      <Dialog
        open={provider !== null && !confirmClear}
        onClose={onClose}
        title={`${provider?.name ?? ""} credentials`}
        description="Stored encrypted on the server. Saved values are never shown again, to anyone."
        footer={
          <>
            {provider?.configured && (
              <Button variant="ghost" className="mr-auto text-danger" onClick={() => setConfirmClear(true)}>
                Remove credentials
              </Button>
            )}
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" form="credentials-form" loading={save.isPending}>
              Save
            </Button>
          </>
        }
      >
        <form id="credentials-form" onSubmit={submit} className="space-y-4" autoComplete="off">
          {provider?.configured && (
            <Notice>
              Credentials are already stored (updated {formatRelative(provider.credentials_updated_at)}). Saving replaces
              all of them, so fill in every field you need.
            </Notice>
          )}
          {fields.map((field) => (
            <Field
              key={field.name}
              label={`${field.label}${field.required ? "" : " (optional)"}`}
              hint={[field.help, stored.has(field.name) ? "A value is stored." : ""].filter(Boolean).join(" ")}
            >
              {(props) =>
                field.kind === "textarea" ? (
                  <Textarea
                    {...props}
                    rows={6}
                    spellCheck={false}
                    className="font-mono text-xs"
                    required={field.required}
                    value={values[field.name] ?? ""}
                    onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
                  />
                ) : (
                  <Input
                    {...props}
                    type={field.kind === "password" ? "password" : "text"}
                    autoComplete={field.kind === "password" ? "new-password" : "off"}
                    required={field.required}
                    value={values[field.name] ?? ""}
                    onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
                  />
                )
              }
            </Field>
          ))}
          {save.isError && <ErrorNote>{errorMessage(save.error)}</ErrorNote>}
        </form>
      </Dialog>
      <ConfirmDialog
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        title="Remove stored credentials?"
        message={`${provider?.name ?? "This provider"} will go back to working without an account, where it supports that.`}
        confirmLabel="Remove"
        destructive
        loading={clear.isPending}
        onConfirm={() => clear.mutate()}
      />
    </>
  );
}

function ErrorsDialog({ provider, onClose }: { provider: Provider | null; onClose: () => void }) {
  const errors = useQuery({
    queryKey: ["providers", "errors", provider?.id],
    queryFn: () => api.get<ProviderError[]>(`/providers/${provider!.id}/errors`),
    enabled: provider !== null,
  });
  return (
    <Dialog open={provider !== null} onClose={onClose} title={`${provider?.name ?? ""} events`} description="Recent provider errors and connection tests." size="lg">
      {errors.isPending ? (
        <PageLoader />
      ) : errors.isError ? (
        <ErrorNote>{errorMessage(errors.error)}</ErrorNote>
      ) : errors.data.length === 0 ? (
        <EmptyState title="Nothing recorded" description="Errors reported by this provider will be listed here." />
      ) : (
        <ul className="divide-y divide-line">
          {errors.data.map((item) => (
            <li key={item.id} className="flex items-start gap-3 py-2.5">
              <span className={cn("mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full", item.level === "error" ? "bg-danger" : item.level === "warning" ? "bg-warning" : "bg-subtle")} />
              <div className="min-w-0 flex-1">
                <p className="break-words text-[13px] leading-5 text-fg">{item.message}</p>
                <p className="text-2xs text-subtle">{[item.code, item.username].filter(Boolean).join(" · ")}</p>
              </div>
              <time className="tabular shrink-0 text-2xs text-subtle">{formatDateTime(item.created_at)}</time>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}

function ProviderCard({ provider, onCredentials, onErrors }: { provider: Provider; onCredentials: () => void; onErrors: () => void }) {
  const toast = useToast();
  const client = useQueryClient();
  const [result, setResult] = useState<ProviderTestResult | null>(null);
  const status = statusOf(provider);
  const refresh = () => void client.invalidateQueries({ queryKey: ["providers"] });

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.patch<Provider>(`/providers/${provider.id}`, { enabled }),
    onSuccess: refresh,
    onError: (error) => toast.error(error),
  });
  const test = useMutation({
    mutationFn: () => api.post<ProviderTestResult>(`/providers/${provider.id}/test`),
    onSuccess: (data) => {
      setResult(data);
      refresh();
    },
    onError: (error) => toast.error(error),
  });

  const account = Object.entries(result?.account ?? {}).filter(([, value]) => value !== null && value !== "");
  const canConfigure = (provider.credential_fields?.length ?? 0) > 0;

  return (
    <Card className="flex flex-col p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-[15px] font-semibold text-fg">{provider.name}</h2>
            <Badge tone={status.tone} dot>
              {status.label}
            </Badge>
          </div>
          <p className="mt-1 text-[13px] leading-5 text-muted">{provider.description}</p>
        </div>
        {provider.homepage && (
          <a href={provider.homepage} target="_blank" rel="noreferrer noopener" aria-label={`Open ${provider.name} website`} className="shrink-0 rounded p-1 text-subtle hover:bg-raised hover:text-fg">
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        )}
      </div>

      <ul className="mt-4 grid grid-cols-1 gap-x-4 gap-y-1.5 sm:grid-cols-2">
        {CAPABILITY_LABELS.map(([key, label]) => (
          <li key={key} className={cn("flex items-center gap-2 text-xs", provider.capabilities[key] ? "text-fg" : "text-subtle")}>
            {provider.capabilities[key] ? <Check className="h-3.5 w-3.5 text-success" /> : <Minus className="h-3.5 w-3.5" />}
            {label}
          </li>
        ))}
      </ul>

      {(provider.capabilities.requires_credentials || provider.capabilities.captcha || provider.capabilities.waits) && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {provider.capabilities.requires_credentials && <Badge tone="warning">Requires an account</Badge>}
          {provider.capabilities.waits && <Badge>May impose waiting periods</Badge>}
          {provider.capabilities.captcha && <Badge>CAPTCHA links are not downloaded</Badge>}
        </div>
      )}

      {provider.notes.length > 0 && (
        <ul className="mt-4 list-disc space-y-1 pl-4 text-xs leading-5 text-muted">
          {provider.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}

      <p className="mt-4 rounded border border-line bg-raised/40 px-3 py-2 text-xs leading-5 text-subtle">
        <span className="font-medium text-muted">Verification: </span>
        {provider.verification}
      </p>

      <div className="mt-4 space-y-3 border-t border-line pt-4">
        <Switch
          label="Enabled"
          description="Disabled providers reject new links. Running downloads are not interrupted."
          checked={provider.enabled}
          disabled={toggle.isPending}
          onChange={(enabled) => toggle.mutate(enabled)}
        />
        <div className="flex flex-wrap items-center gap-2">
          {canConfigure && (
            <Button size="sm" variant="secondary" icon={<KeyRound className="h-3.5 w-3.5" />} onClick={onCredentials}>
              {provider.configured ? "Change credentials" : "Add credentials"}
            </Button>
          )}
          <Button size="sm" variant="secondary" icon={<PlugZap className="h-3.5 w-3.5" />} loading={test.isPending} onClick={() => test.mutate()}>
            Test connection
          </Button>
          <Button size="sm" variant="ghost" icon={<ScrollText className="h-3.5 w-3.5" />} onClick={onErrors}>
            Events
          </Button>
        </div>
        {test.isPending && (
          <p className="flex items-center gap-2 text-xs text-subtle">
            <Spinner className="h-3.5 w-3.5" /> Contacting {provider.name}…
          </p>
        )}
        {result && !test.isPending ? (
          <Notice tone={result.ok ? "success" : "warning"}>
            {result.message}
            {account.length > 0 && (
              <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                {account.map(([key, value]) => (
                  <div key={key} className="contents">
                    <dt className="capitalize opacity-80">{key.replace(/_/g, " ")}</dt>
                    <dd className="tabular truncate">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </Notice>
        ) : (
          provider.health !== "unknown" &&
          provider.health_message && (
            <p className={cn("text-xs leading-5", provider.health === "ok" ? "text-subtle" : "text-danger")}>
              Last test {formatRelative(provider.checked_at)}: {provider.health_message}
            </p>
          )
        )}
      </div>
    </Card>
  );
}

export function ProvidersPage() {
  const providers = useProviders();
  const [credentialsFor, setCredentialsFor] = useState<string | null>(null);
  const [errorsFor, setErrorsFor] = useState<string | null>(null);
  const find = (id: string | null) => providers.data?.find((item) => item.id === id) ?? null;

  return (
    <>
      <PageHeader
        title="Providers"
        description="What each link source supports, and the accounts FETCH may use for it."
      />
      <Notice className="mb-4">
        FETCH only downloads what a provider permits for the link and account in use. It does not solve CAPTCHAs, skip
        waiting periods, or work around Premium and quota limits.
      </Notice>
      {providers.isPending ? (
        <PageLoader />
      ) : providers.isError ? (
        <ErrorNote>{errorMessage(providers.error)}</ErrorNote>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {providers.data.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              onCredentials={() => setCredentialsFor(provider.id)}
              onErrors={() => setErrorsFor(provider.id)}
            />
          ))}
        </div>
      )}
      <CredentialsDialog provider={find(credentialsFor)} onClose={() => setCredentialsFor(null)} />
      <ErrorsDialog provider={find(errorsFor)} onClose={() => setErrorsFor(null)} />
    </>
  );
}
