import { AppWindow, ExternalLink } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { Button, Dialog, ErrorNote, Field, Input, Notice, PageLoader, useToast } from "@/components/ui";
import { buildUrl, errorMessage } from "@/lib/api";
import { useCaptcha, useOpenCaptchaWindow, useSubmitCaptcha } from "@/lib/queries";
import type { Download } from "@/lib/types";

interface Props {
  download: Download | null;
  onClose: () => void;
}

/**
 * Lets the person complete a provider's CAPTCHA themselves. FETCH only shows
 * the provider's challenge and passes the typed answer on; it never solves one.
 */
export function CaptchaDialog({ download, onClose }: Props) {
  const toast = useToast();
  const captcha = useCaptcha(download?.id ?? null);
  const submit = useSubmitCaptcha();
  const openWindow = useOpenCaptchaWindow();
  const [answer, setAnswer] = useState("");
  const [address, setAddress] = useState("");
  const challenge = captcha.data;

  useEffect(() => {
    setAnswer("");
    setAddress("");
    submit.reset();
    openWindow.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [download?.id, challenge?.attempt]);

  const send = (event: FormEvent) => {
    event.preventDefault();
    if (!download) return;
    const body = address.trim() ? { download_url: address.trim() } : { answer: answer.trim() };
    submit.mutate(
      { id: download.id, ...body },
      {
        onSuccess: () => {
          toast.success("Sent to the provider. The download continues if it accepts.");
          onClose();
        },
      },
    );
  };

  const openInApp = () => {
    if (!download) return;
    openWindow.mutate(download.id, {
      onSuccess: () => toast.success("Complete the CAPTCHA in the window that opened. The download continues by itself."),
    });
  };

  const canSend = address.trim() !== "" || (challenge?.kind === "image" && answer.trim() !== "");

  return (
    <Dialog
      open={download !== null}
      onClose={onClose}
      title="Complete the CAPTCHA"
      description={download?.filename || download?.url}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button variant="primary" type="submit" form="captcha-form" loading={submit.isPending} disabled={!canSend}>
            Continue download
          </Button>
        </>
      }
    >
      {captcha.isPending ? (
        <PageLoader />
      ) : captcha.isError || !challenge ? (
        <ErrorNote>
          This CAPTCHA is no longer available. If the download failed, retry it to get a new one.
        </ErrorNote>
      ) : (
        <form id="captcha-form" onSubmit={send} className="space-y-4" autoComplete="off">
          {challenge.in_app ? (
            <div className="space-y-3">
              <Notice tone="warning">
                This CAPTCHA only works on the provider's own page. Open it in a FETCH window, complete the free
                download steps and the CAPTCHA there yourself, and FETCH takes the download over when the download
                button appears.
              </Notice>
              <Button variant="primary" icon={<AppWindow className="h-4 w-4" />} loading={openWindow.isPending} onClick={openInApp}>
                Open the CAPTCHA page
              </Button>
              {openWindow.isError && <ErrorNote>{errorMessage(openWindow.error)}</ErrorNote>}
            </div>
          ) : (
            <Notice tone="warning">{challenge.prompt}</Notice>
          )}
          {challenge.kind === "image" && challenge.has_image && download && (
            <>
              <img
                src={buildUrl(`/downloads/${download.id}/captcha/image`, { attempt: challenge.attempt })}
                alt="CAPTCHA picture from the provider"
                className="max-h-40 rounded border border-line bg-white p-2"
              />
              <Field label="Characters in the picture" hint={challenge.attempt > 1 ? `Attempt ${challenge.attempt}. The previous answer was not accepted.` : undefined}>
                {(props) => (
                  <Input {...props} autoFocus value={answer} onChange={(event) => setAnswer(event.target.value)} />
                )}
              </Field>
            </>
          )}
          {challenge.page_url && (
            <p className="text-xs leading-5 text-muted">
              <a href={challenge.page_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 font-medium text-fg underline">
                {challenge.in_app ? "Or open it in your browser instead" : "Open the file on the provider's website"}{" "}
                <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          )}
          {challenge.accepts_download_url && (
            <Field
              label={challenge.kind === "image" || challenge.in_app ? "Or paste the download address (optional)" : "Download address from the provider"}
              hint="After you finish the provider's free download steps in your browser, copy the address of its final download button and paste it here. It only works from the same network as your browser."
            >
              {(props) => (
                <Input {...props} type="url" placeholder="https://…" value={address} onChange={(event) => setAddress(event.target.value)} />
              )}
            </Field>
          )}
          {submit.isError && <ErrorNote>{errorMessage(submit.error)}</ErrorNote>}
        </form>
      )}
    </Dialog>
  );
}
