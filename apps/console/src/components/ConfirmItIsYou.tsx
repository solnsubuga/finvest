import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { setConfirmer, type ConfirmationApi } from "@/api/confirmation.ts";
import { ApiError } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { ErrorNote, KeyIcon } from "@/components/AuthParts.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";

type Method = "passkey" | "authenticator";

/** What each refusal from the confirmation routes means to the person. */
export function confirmationErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    // The browser's own prompt was cancelled or timed out.
    return "Your passkey was not used. Try again when you are ready.";
  }
  switch (error.code) {
    case "invalid_code":
      return "That code is wrong or has already been used. Enter the newest code from your app.";
    case "passkey_failed":
      return "That passkey did not work. Try again, or use another of yours.";
    case "expired":
      return "That took too long. Try again.";
    case "rate_limited": {
      const minutes = error.retryAfterSeconds ? Math.max(1, Math.ceil(error.retryAfterSeconds / 60)) : null;
      return minutes
        ? `Too many wrong codes. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`
        : "Too many wrong codes. Wait a few minutes and try again.";
    }
    default:
      return "We could not reach Finvest. Check your connection and try again.";
  }
}

interface ConfirmItIsYouProps {
  /** Null in the sample sign-in, which never asks. */
  api: ConfirmationApi | null;
  children?: ReactNode;
}

/**
 * Asks the signed-in member of staff for their passkey or an authenticator
 * code when the API wants them to confirm it is them before a decision. The
 * refused request is sent again by `createHttp` once they have.
 */
export function ConfirmItIsYou({ api, children }: ConfirmItIsYouProps) {
  const { session } = useAuth();
  const held = session?.secondSteps ?? [];
  // An API that does not list them yet: offer both, and let it refuse the one the person lacks.
  const offered: readonly Method[] = held.length > 0 ? held : ["passkey", "authenticator"];
  const first = offered[0] ?? "passkey";

  const [open, setOpen] = useState(false);
  const [method, setMethod] = useState<Method>(first);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const answer = useRef<((confirmed: boolean) => void) | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const codeId = useId();
  const errorId = useId();
  const titleId = useId();

  const finish = useCallback((confirmed: boolean) => {
    answer.current?.(confirmed);
    answer.current = null;
    setOpen(false);
    setBusy(false);
  }, []);

  const signedIn = session !== null;
  useEffect(() => {
    if (!api || !signedIn) return;
    const remove = setConfirmer(
      () =>
        new Promise<boolean>((resolve) => {
          answer.current = resolve;
          setMethod(first);
          setCode("");
          setError(null);
          setOpen(true);
        }),
    );
    return () => {
      remove();
      // Signing out while the dialog is up leaves the refused request refused.
      answer.current?.(false);
      answer.current = null;
      setOpen(false);
    };
  }, [api, signedIn, first]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open]);

  async function confirmPasskey() {
    if (!api) return;
    setBusy(true);
    setError(null);
    try {
      await api.withPasskey();
      finish(true);
    } catch (caught) {
      setError(confirmationErrorMessage(caught));
      setBusy(false);
    }
  }

  async function submitCode(event: FormEvent) {
    event.preventDefault();
    if (!api) return;
    if (!/^\d{6}$/.test(code)) {
      setError("Enter the 6-digit code from your authenticator app.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.withCode(code);
      finish(true);
    } catch (caught) {
      setError(confirmationErrorMessage(caught));
      setCode("");
      setBusy(false);
    }
  }

  function switchMethod(next: Method) {
    setMethod(next);
    setCode("");
    setError(null);
  }

  const other = offered.find((candidate) => candidate !== method);

  return (
    <>
      {children}
      <dialog
        ref={dialogRef}
        aria-labelledby={titleId}
        onCancel={(event) => {
          // Escape: close, unless a passkey prompt is already open.
          event.preventDefault();
          if (!busy) finish(false);
        }}
        className="m-auto w-[min(440px,calc(100vw-32px))] rounded-2xl border border-border bg-surface p-6 text-ink backdrop:bg-black/50"
      >
        {open ? (
          <div className="flex flex-col">
            <h2 id={titleId} className="m-0 text-2xl leading-tight font-extrabold tracking-[-0.4px]">
              Confirm it is you
            </h2>
            {method === "passkey" ? (
              <>
                <p className="mt-2 mb-0 text-md leading-normal text-muted">
                  Deciding someone else&apos;s change needs your passkey. Your browser will ask for your
                  fingerprint, face or device PIN.
                </p>
                {error ? <ErrorNote id={errorId} message={error} /> : null}
                <Button size="lg" autoFocus disabled={busy} onClick={() => void confirmPasskey()} className="mt-5">
                  <KeyIcon />
                  {busy ? "Waiting for your passkey" : "Use passkey"}
                </Button>
              </>
            ) : (
              <form onSubmit={(event) => void submitCode(event)} noValidate className="flex flex-col">
                <p className="mt-2 mb-0 text-md leading-normal text-muted">
                  Deciding someone else&apos;s change needs a code. Open your authenticator app and enter the
                  6-digit code for Finvest.
                </p>
                <div className="mt-4 flex flex-col gap-1.5">
                  <Label htmlFor={codeId}>6-digit code</Label>
                  <Input
                    id={codeId}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    autoFocus
                    value={code}
                    onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? errorId : undefined}
                    className="text-2xl tracking-[0.3em] tabular-nums"
                  />
                </div>
                {error ? <ErrorNote id={errorId} message={error} /> : null}
                <Button type="submit" size="lg" disabled={busy} className="mt-5">
                  {busy ? "Checking" : "Confirm"}
                </Button>
              </form>
            )}
            {other ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => switchMethod(other)}
                className="mt-2 h-11 cursor-pointer self-center text-md font-semibold text-ink underline"
              >
                {other === "authenticator" ? "Use a code from your authenticator app" : "Use your passkey"}
              </button>
            ) : null}
            <button
              type="button"
              disabled={busy}
              onClick={() => finish(false)}
              className="h-11 cursor-pointer self-center text-md font-semibold text-ink underline"
            >
              Cancel
            </button>
          </div>
        ) : null}
      </dialog>
    </>
  );
}
