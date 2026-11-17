import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button, Dialog, ErrorNote, Field, Input, Notice, Segmented, Spinner, Textarea, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { formatBytes, PRIORITIES } from "@/lib/format";
import { useInspect, useSettingsDefaults, useSubmit } from "@/lib/queriesExtra";
import type { LinkInfo, SubmitResult } from "@/lib/types";

import { ProviderTag } from "./parts";

const MAX_LINKS = 100;

export function parseUrls(text: string): string[] {
  const seen = new Set<string>();
  const urls: string[] = [];
  for (const piece of text.split(/[\s,]+/)) {
    const url = piece.trim();
    if (url && !seen.has(url)) {
      seen.add(url);
      urls.push(url);
    }
  }
  return urls;
}

interface Props {
  open: boolean;
  onClose: () => void;
  initialFolder?: string;
}

export function AddDownloadDialog({ open, onClose, initialFolder = "" }: Props) {
  const toast = useToast();
  const defaults = useSettingsDefaults();
  const inspect = useInspect();
  const submit = useSubmit();

  const [text, setText] = useState("");
  const [folder, setFolder] = useState("");
  const [priority, setPriority] = useState(1);
  const [inspected, setInspected] = useState<Record<string, LinkInfo>>({});
  const [result, setResult] = useState<SubmitResult | null>(null);
  const requestId = useRef(0);

  const urls = useMemo(() => parseUrls(text), [text]);
  const tooMany = urls.length > MAX_LINKS;

  // Reset whenever the dialog opens.
  useEffect(() => {
    if (!open) return;
    setText("");
    setInspected({});
    setResult(null);
    setFolder(initialFolder);
    setPriority(defaults.priority);
    inspect.reset();
    submit.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Inspect links shortly after the user stops typing or pastes.
  const inspectMutate = inspect.mutate;
  useEffect(() => {
    if (!open) return;
    const missing = urls.filter((url) => !inspected[url]).slice(0, MAX_LINKS);
    if (missing.length === 0 || tooMany) return;
    const current = ++requestId.current;
    const timer = window.setTimeout(() => {
      inspectMutate(missing, {
        onSuccess: (data) => {
          if (current !== requestId.current) return;
          setInspected((previous) => {
            const next = { ...previous };
            data.items.forEach((item) => (next[item.url] = item));
            return next;
          });
        },
      });
    }, 500);
    return () => window.clearTimeout(timer);
  }, [urls, inspected, open, tooMany, inspectMutate]);

  const rows = urls.map((url) => ({ url, info: inspected[url] }));
  const supported = rows.filter((row) => row.info?.supported).length;
  const unsupported = rows.filter((row) => row.info && !row.info.supported).length;
  const pending = rows.some((row) => !row.info);
  const knownSize = rows.reduce((sum, row) => sum + (row.info?.supported ? (row.info.size ?? 0) : 0), 0);
  const canSubmit = urls.length > 0 && !tooMany && !pending && supported > 0 && !submit.isPending;

  const onSubmit = () => {
    const accepted = rows.filter((row) => row.info?.supported).map((row) => row.url);
    submit.mutate(
      { urls: accepted, destination: "private", folder: folder.trim(), priority },
      {
        onSuccess: (data) => {
          if (data.rejected.length === 0) {
            toast.success(`${data.created.length} download${data.created.length === 1 ? "" : "s"} added to the queue.`);
            onClose();
          } else {
            setResult(data);
          }
        },
      },
    );
  };

  const priorities = PRIORITIES.map((p) => ({ value: p.value, label: p.label }));

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add downloads"
      description="Paste one or more links. FETCH detects the provider and checks each link before queueing it."
      size="lg"
      footer={
        result ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <span className="mr-auto text-xs text-subtle">
              {urls.length === 0
                ? "No links yet"
                : `${supported} ready${unsupported ? ` · ${unsupported} cannot be added` : ""}${knownSize ? ` · ${formatBytes(knownSize)}` : ""}`}
            </span>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" onClick={onSubmit} disabled={!canSubmit} loading={submit.isPending}>
              {supported > 1 ? `Add ${supported} downloads` : "Add download"}
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="space-y-3">
          <Notice tone={result.created.length ? "success" : "warning"}>
            {result.created.length} download{result.created.length === 1 ? "" : "s"} added. {result.rejected.length} link
            {result.rejected.length === 1 ? " was" : "s were"} not accepted:
          </Notice>
          <ul className="space-y-2">
            {result.rejected.map((item) => (
              <li key={item.url} className="rounded border border-line bg-canvas px-3 py-2">
                <p className="truncate font-mono text-xs text-muted">{item.url}</p>
                <p className="mt-1 text-xs text-danger">{item.message}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="space-y-4">
          <Field label="Links" hint={`One per line. Up to ${MAX_LINKS} at a time.`} error={tooMany ? `That is ${urls.length} links; the limit is ${MAX_LINKS}.` : null}>
            {(props) => (
              <Textarea
                {...props}
                data-autofocus
                rows={4}
                spellCheck={false}
                placeholder={"https://example.com/file.zip\nhttps://mega.nz/file/…#…"}
                className="resize-y font-mono text-xs"
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            )}
          </Field>

          {rows.length > 0 && !tooMany && (
            <ul className="max-h-56 divide-y divide-line overflow-y-auto rounded border border-line bg-canvas" aria-label="Link inspection">
              {rows.map(({ url, info }) => (
                <li key={url} className="flex items-start gap-3 px-3 py-2">
                  <span className="mt-0.5 shrink-0">
                    {!info ? (
                      <Spinner className="h-3.5 w-3.5" />
                    ) : !info.supported ? (
                      <XCircle className="h-3.5 w-3.5 text-danger" />
                    ) : info.restriction ? (
                      <AlertTriangle className="h-3.5 w-3.5 text-warning" />
                    ) : (
                      <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-3">
                      <p className="min-w-0 flex-1 truncate text-[13px] text-fg" title={url}>
                        {info?.filename || url}
                      </p>
                      {info?.provider && <ProviderTag id={info.provider} />}
                      <span className="tabular w-16 shrink-0 text-right text-xs text-subtle">
                        {info ? (info.supported ? formatBytes(info.size) : "") : ""}
                      </span>
                    </div>
                    {info?.filename && <p className="truncate font-mono text-2xs text-subtle">{url}</p>}
                    {info?.message && (
                      <p className={`mt-1 text-xs leading-4 ${info.supported ? "text-warning" : "text-danger"}`}>{info.message}</p>
                    )}
                    {!info && inspect.isError && <p className="mt-1 text-xs text-danger">{errorMessage(inspect.error)}</p>}
                  </div>
                </li>
              ))}
            </ul>
          )}

          <div>
            <Field label="Subfolder (optional)" hint="For example: movies/2024. It is created if needed.">
              {(props) => <Input {...props} value={folder} onChange={(event) => setFolder(event.target.value)} placeholder="Top level" spellCheck={false} />}
            </Field>
          </div>

          <div>
            <p className="mb-1.5 text-xs font-medium text-muted">Priority</p>
            <Segmented label="Priority" value={priority} onChange={setPriority} options={priorities} />
          </div>

          <ErrorNote>{submit.isError ? errorMessage(submit.error) : null}</ErrorNote>
        </div>
      )}
    </Dialog>
  );
}
