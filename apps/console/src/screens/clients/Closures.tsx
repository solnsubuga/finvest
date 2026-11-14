import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { formatAccountNumber, formatAmount, formatTime } from "./labels.ts";
import { ReasonForm } from "./Parts.tsx";
import { closuresErrorMessage, ClosuresError, type Closure, type ClosuresApi, type Standing } from "./closures.ts";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; items: Closure[] };

function useOpenClosures(api: ClosuresApi) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const refresh = useCallback(
    () =>
      api.open().then(
        (items) => setLoad({ state: "ready", items }),
        (failure: unknown) => setLoad({ state: "failed", message: closuresErrorMessage(failure) }),
      ),
    [api],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);
  return { load, refresh };
}

const STATUS_WORDS: Record<Closure["status"], string> = {
  requested: "Waiting for a first check",
  prepared: "Checked once, waiting for a second person",
  closed: "Closed",
  declined: "Declined",
  cancelled: "Cancelled by the client",
};

/** What is still open for the client, in the words staff use. Empty when nothing is. */
export function standingLines(standing: Standing): string[] {
  const lines: string[] = [];
  const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  if (standing.runningInvestments) lines.push(count(standing.runningInvestments, "investment running", "investments running"));
  for (const owed of standing.owed) lines.push(`${formatAmount(owed.minor, owed.currency)} still owed to the client`);
  if (standing.openDeposits) lines.push(count(standing.openDeposits, "payment in on its way", "payments in on their way"));
  if (standing.openPayouts) lines.push(count(standing.openPayouts, "payout on its way", "payouts on their way"));
  if (standing.openGoals) lines.push(count(standing.openGoals, "goal open", "goals open"));
  if (standing.livePlans) lines.push(count(standing.livePlans, "automatic plan on", "automatic plans on"));
  return lines;
}

/** The queue of open closure requests, on the Clients landing page. Hidden from roles that cannot close accounts. */
export function ClosureQueue({ api, onOpen }: { api: ClosuresApi; onOpen: (clientId: string) => void }) {
  const { load, refresh } = useOpenClosures(api);
  if (load.state === "failed" && load.message === closuresErrorMessage(new ClosuresError("forbidden"))) return null;
  return (
    <section aria-labelledby="closures" className="flex max-w-[720px] flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id="closures" className="m-0 text-xl font-extrabold">
          Accounts to close
        </h2>
        <p className="m-0 text-md leading-normal text-muted">
          Clients who asked to close their account. One person checks nothing is left and prepares it; a different person approves, and only then is the account closed.
        </p>
      </div>
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-sm text-muted">
          Loading requests
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex flex-col items-start gap-2 rounded-xl border border-border bg-surface p-4">
          <p className="m-0 text-sm font-semibold">{load.message}</p>
          <Button variant="secondary" onClick={() => void refresh()}>
            Try again
          </Button>
        </div>
      ) : load.items.length === 0 ? (
        <p className="m-0 rounded-xl border border-border bg-surface p-4 text-sm text-muted">No client is waiting to close their account.</p>
      ) : (
        <ul aria-label="Requests to close an account" className="m-0 flex list-none flex-col rounded-xl border border-border bg-surface px-4 py-1">
          {load.items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3 last:border-b-0">
              <div className="flex min-w-0 flex-col gap-0.5 text-sm">
                <span className="font-bold">
                  {item.verifiedName ?? "Name not verified"}
                  {item.accountNumber ? <span className="font-medium text-muted"> · {formatAccountNumber(item.accountNumber)}</span> : null}
                </span>
                <span className="text-muted">
                  Asked {formatTime(item.requestedAt)} · {STATUS_WORDS[item.status]}
                </span>
              </div>
              <Button variant="secondary" onClick={() => onOpen(item.clientId)}>
                Open
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

type Step = "prepare" | "approve" | "decline";

const STEP_WORDS: Record<Step, { label: string; hint: string; submit: string }> = {
  prepare: {
    label: "What did you check?",
    hint: "The second person reads this, and it goes in the audit trail with your name.",
    submit: "Prepare for closing",
  },
  approve: {
    label: "Why approve?",
    hint: "Approving closes the account at once and signs the client out everywhere. It cannot be undone.",
    submit: "Approve and close",
  },
  decline: {
    label: "Why decline?",
    hint: "The client reads this note, so write it for them.",
    submit: "Decline",
  },
};

/** The client's open request to close, if any, with the next step for this reader. */
export function ClosureSection({ api, clientId, onClosed }: { api: ClosuresApi; clientId: string; onClosed: () => void }) {
  const { load, refresh } = useOpenClosures(api);
  const [step, setStep] = useState<Step | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (load.state !== "ready") return null;
  const item = load.items.find((i) => i.clientId === clientId);
  if (!item) {
    return notice ? (
      <p role="status" className="m-0 max-w-[720px] rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
        {notice}
      </p>
    ) : null;
  }
  const open = standingLines(item.standing);

  async function act(chosen: Step, note: string) {
    if (!item) return;
    setBusy(true);
    setError(null);
    try {
      const done = await api[chosen](item.id, note);
      setStep(null);
      setNotice(
        done.status === "closed"
          ? "Approved. The account is closed and the client has been signed out everywhere."
          : done.status === "declined"
            ? "Declined. The account stays open and the client sees your note."
            : "Prepared. A different person must approve it before the account closes.",
      );
      if (done.status === "closed") onClosed();
      await refresh();
    } catch (failure) {
      setError(closuresErrorMessage(failure));
      if (failure instanceof ClosuresError && ["blocked", "moved", "not_found"].includes(failure.code)) await refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="closure" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id="closure" className="m-0 text-xl font-extrabold">
          Request to close the account
        </h2>
        <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
          The client asked for this themselves. Money never moves here: anything still open must be finished first.
        </p>
      </div>
      {notice ? (
        <p role="status" className="m-0 max-w-[720px] rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
          {notice}
        </p>
      ) : null}
      <article aria-label="Request to close, open" className="flex max-w-[720px] flex-col gap-3 rounded-xl border border-border bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="m-0 text-[17px] font-extrabold">Asked {formatTime(item.requestedAt)}</h3>
          <span className="rounded-pill bg-warning-surface px-2 py-0.5 text-2xs font-bold text-warning-text">{STATUS_WORDS[item.status]}</span>
        </div>
        <p className="m-0 text-md">{item.reason ? `“${item.reason}”` : <span className="text-muted">The client gave no reason.</span>}</p>
        {item.preparedByName ? (
          <p className="m-0 text-sm text-muted">
            Prepared by {item.preparedByName}
            {item.preparedAt ? `, ${formatTime(item.preparedAt)}` : ""}
            {item.preparedNote ? `: “${item.preparedNote}”` : ""}
          </p>
        ) : null}
        {open.length ? (
          <div role="note" className="flex flex-col gap-1 rounded-md bg-danger-surface px-3 py-2.5 text-sm">
            <span className="font-bold">Still open, so it cannot close yet</span>
            <ul className="m-0 pl-5">
              {open.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="m-0 rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">Nothing is open: no investments, money owed, payments, goals or plans.</p>
        )}
        {step ? (
          <ReasonForm
            {...STEP_WORDS[step]}
            busy={busy}
            error={error}
            onSubmit={(note) => void act(step, note)}
            onCancel={() => {
              setStep(null);
              setError(null);
            }}
          />
        ) : (
          <>
            {error ? (
              <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
                {error}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              {item.canPrepare ? (
                <Button disabled={open.length > 0} onClick={() => setStep("prepare")}>
                  Prepare for closing
                </Button>
              ) : null}
              {item.canApprove ? (
                <Button disabled={open.length > 0} onClick={() => setStep("approve")}>
                  Approve and close
                </Button>
              ) : null}
              {item.canDecline ? (
                <Button variant="secondary" onClick={() => setStep("decline")}>
                  Decline
                </Button>
              ) : null}
            </div>
            {item.status === "prepared" && !item.canApprove ? (
              <p className="m-0 text-xs text-muted">You prepared this, so someone else approves it.</p>
            ) : null}
          </>
        )}
      </article>
    </section>
  );
}
