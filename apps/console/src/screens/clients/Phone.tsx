import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { formatTime } from "./labels.ts";
import { INPUT, ReasonForm } from "./Parts.tsx";
import { normalisePhone, PhoneChangeError, phoneChangeErrorMessage, type PhoneChange, type PhoneChangesApi } from "./phone.ts";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; waiting: PhoneChange | null };
type Form = "propose" | "reject" | null;

/** "+256701234567" as "+256 701 234 567". */
export function formatPhone(phone: string): string {
  const match = /^\+256(\d{3})(\d{3})(\d{3})$/.exec(phone);
  return match ? `+256 ${match[1]} ${match[2]} ${match[3]}` : phone;
}

/**
 * The client's mobile number: propose a new one, or decide one a colleague proposed. Shown
 * only to staff with `client_phones.change`; the backend checks it again.
 */
export function PhoneSection({ api, clientId, currentPhone, onChanged }: { api: PhoneChangesApi; clientId: string; currentPhone: string; onChanged: () => void }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [form, setForm] = useState<Form>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchWaiting = useCallback(
    (): Promise<Load> =>
      api.waitingFor(clientId).then(
        (waiting) => ({ state: "ready", waiting }),
        (failure: unknown) => ({ state: "failed", message: phoneChangeErrorMessage(failure) }),
      ),
    [api, clientId],
  );

  useEffect(() => {
    let current = true;
    void fetchWaiting().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchWaiting]);

  async function run(command: () => Promise<void>, done: string, changesNumber = false) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await command();
      setForm(null);
      setNotice(done);
      if (changesNumber) onChanged();
    } catch (failure) {
      setError(phoneChangeErrorMessage(failure));
      // Decided or lapsed while it was on screen: show where it stands now.
      if (!(failure instanceof PhoneChangeError) || !["not_found", "expired", "waiting"].includes(failure.code)) return;
    } finally {
      setBusy(false);
    }
    const fresh = await fetchWaiting();
    setLoad(fresh);
  }

  const waiting = load.state === "ready" ? load.waiting : null;
  return (
    <section aria-labelledby="mobile-number" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id="mobile-number" className="m-0 text-xl font-extrabold">
          Mobile number
        </h2>
        <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
          The number is how the client proves who they are, so one person proposes a new one and a different person approves it. When it changes,
          the client is signed out everywhere, payouts go only to accounts they already use for 24 hours, and the old number and their email are
          told.
        </p>
      </div>
      {notice ? (
        <p role="status" className="m-0 max-w-[720px] rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
          {notice}
        </p>
      ) : null}
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-sm text-muted">
          Checking for a change waiting
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex max-w-[720px] flex-col items-start gap-2 rounded-xl border border-border bg-surface p-4">
          <p className="m-0 text-md text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchWaiting().then(setLoad)}>
            Try again
          </Button>
        </div>
      ) : waiting ? (
        <article aria-label="New mobile number, waiting" className="flex max-w-[720px] flex-col gap-3 rounded-xl border border-border bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-[17px] font-extrabold">
              {formatPhone(currentPhone)} to <span className="tabular-nums">{formatPhone(waiting.newPhone)}</span>
            </h3>
            <span className="rounded-pill bg-warning-surface px-2 py-0.5 text-2xs font-bold text-warning-text">Waiting for a second person</span>
          </div>
          <p className="m-0 text-md">“{waiting.reason}”</p>
          <p className="m-0 text-sm text-muted">
            Proposed by {waiting.yours ? "you" : (waiting.proposedByName ?? "a colleague")}, {formatTime(waiting.proposedAt)}. Lapses{" "}
            {formatTime(waiting.expiresAt)} if nobody decides.
          </p>
          {form === "reject" ? (
            <ReasonForm
              label="Why reject?"
              hint="The person who proposed it reads this, and it goes in the audit trail."
              submit="Reject"
              busy={busy}
              error={error}
              onSubmit={(note) => void run(() => api.reject(waiting.id, note), "Rejected. The number did not change.")}
              onCancel={() => setForm(null)}
            />
          ) : (
            <>
              {error ? (
                <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
                  {error}
                </p>
              ) : null}
              {waiting.yours ? (
                <p className="m-0 text-xs text-muted">You proposed this, so someone else approves or rejects it.</p>
              ) : (
                <>
                  <p className="m-0 text-sm leading-normal">Before approving, call the client on the new number and check who they are.</p>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void run(
                          () => api.approve(waiting.id),
                          `Approved. The number is now ${formatPhone(waiting.newPhone)} and the client has been signed out everywhere.`,
                          true,
                        )
                      }
                    >
                      {busy ? "Approving" : "Approve new number"}
                    </Button>
                    <Button variant="secondary" disabled={busy} onClick={() => setForm("reject")}>
                      Reject
                    </Button>
                  </div>
                </>
              )}
            </>
          )}
        </article>
      ) : form === "propose" ? (
        <ProposeForm
          busy={busy}
          error={error}
          onSubmit={(phone, reason) => void run(() => api.propose(clientId, phone, reason), "Sent. Someone else must approve the new number before it changes.")}
          onCancel={() => {
            setForm(null);
            setError(null);
          }}
        />
      ) : (
        <div className="flex flex-col items-start gap-2">
          {error ? (
            <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
              {error}
            </p>
          ) : null}
          <Button variant="secondary" onClick={() => setForm("propose")}>
            Change mobile number
          </Button>
        </div>
      )}
    </section>
  );
}

function ProposeForm({ busy, error, onSubmit, onCancel }: { busy: boolean; error: string | null; onSubmit: (phone: string, reason: string) => void; onCancel: () => void }) {
  const [phone, setPhone] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const id = useId();
  const phoneWrong = tried && normalisePhone(phone) === null;
  const reasonMissing = tried && !reason.trim();

  function submit(event: FormEvent) {
    event.preventDefault();
    setTried(true);
    if (normalisePhone(phone) !== null && reason.trim()) onSubmit(phone.trim(), reason.trim());
  }

  return (
    <form aria-label="Propose a new mobile number" onSubmit={submit} className="flex max-w-[720px] flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <h3 className="m-0 text-[17px] font-extrabold">New mobile number</h3>
      <label htmlFor={`${id}-phone`} className="flex flex-col gap-1.5 text-sm font-bold">
        New number
        <span className="text-xs font-medium text-muted">A Ugandan mobile number, such as 0772 123 456.</span>
        <input
          id={`${id}-phone`}
          value={phone}
          inputMode="tel"
          autoComplete="off"
          aria-invalid={phoneWrong}
          onChange={(event) => setPhone(event.target.value)}
          className={INPUT}
        />
        {phoneWrong ? <span className="text-xs font-bold text-danger">Write a Ugandan mobile number, such as 0772 123 456.</span> : null}
      </label>
      <label htmlFor={`${id}-reason`} className="flex flex-col gap-1.5 text-sm font-bold">
        Why?
        <span className="text-xs font-medium text-muted">How you checked it is the client. The second person reads this, and it goes in the audit trail.</span>
        <textarea
          id={`${id}-reason`}
          value={reason}
          rows={2}
          maxLength={500}
          aria-invalid={reasonMissing}
          onChange={(event) => setReason(event.target.value)}
          className={INPUT}
        />
        {reasonMissing ? <span className="text-xs font-bold text-danger">Write a short reason for the audit trail.</span> : null}
      </label>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Sending" : "Send for approval"}
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
