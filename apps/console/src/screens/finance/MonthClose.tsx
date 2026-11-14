import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import type { Currency } from "./api.ts";
import { formatDay, formatMonth, formatNumber } from "./format.ts";
import {
  monthCloseErrorMessage,
  refusalText,
  toMonthCloseError,
  type CloseDecision,
  type CloseStatus,
  type MonthClose,
  type MonthCloseApi,
  type MonthCloses,
} from "./monthClose.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3 py-2.5 text-md font-medium text-ink";
const TIME = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Africa/Kampala" });

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: MonthCloses };

const STATUS: Record<CloseStatus, { label: string; tone: string }> = {
  proposed: { label: "Waiting for approval", tone: "bg-warning-surface text-warning-text" },
  approved: { label: "Closed", tone: "bg-success-surface text-success" },
  declined: { label: "Declined", tone: "bg-surface-sunken text-muted" },
  withdrawn: { label: "Withdrawn", tone: "bg-surface-sunken text-muted" },
};

const DECISIONS: Record<CloseDecision, { button: string; busy: string; done: string }> = {
  approve: { button: "Approve and close", busy: "Closing", done: "is closed. Its books are kept as signed." },
  decline: { button: "Decline", busy: "Declining", done: "was sent back. It can be proposed again." },
  withdraw: { button: "Withdraw", busy: "Withdrawing", done: "was withdrawn. It can be proposed again." },
};

interface MonthCloseSectionProps {
  api: MonthCloseApi;
  /** The reader's own staff ID. */
  me: string;
  newKey: () => string;
}

export function MonthCloseSection({ api, me, newKey }: MonthCloseSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [proposing, setProposing] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const fetch = useCallback(
    (): Promise<Load> =>
      api.getCloses().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: monthCloseErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetch().then((next) => {
      if (current) setLoad(next);
    });
    return () => {
      current = false;
    };
  }, [fetch]);

  const reload = useCallback(async () => {
    setLoad(await fetch());
  }, [fetch]);

  return (
    <section aria-labelledby="month-close" className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex max-w-[720px] flex-col gap-1">
          <h2 id="month-close" className="m-0 text-xl font-extrabold">
            Close the month
          </h2>
          <p className="m-0 text-md leading-normal text-muted">
            Finance's sign-off that a month is finished, once every day in it is closed and its books balance. One person proposes and a different person approves. It moves no
            money, and keeps the month's books exactly as they were signed.
          </p>
        </div>
      </div>
      {note ? (
        <p role="status" className="m-0 rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
          {note}
        </p>
      ) : null}
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading month closes
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex flex-col items-start gap-3">
          <p className="m-0 text-md font-bold">Month closes could not be loaded</p>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetch().then(setLoad);
            }}
          >
            Try again
          </Button>
        </div>
      ) : (
        <Ready
          closes={load.value}
          me={me}
          api={api}
          newKey={newKey}
          proposing={proposing}
          onPropose={(open) => {
            setProposing(open);
            setNote(null);
          }}
          onDone={async (message) => {
            setProposing(false);
            setNote(message);
            await reload();
          }}
          onStale={reload}
        />
      )}
    </section>
  );
}

function Ready({
  closes,
  me,
  api,
  newKey,
  proposing,
  onPropose,
  onDone,
  onStale,
}: {
  closes: MonthCloses;
  me: string;
  api: MonthCloseApi;
  newKey: () => string;
  proposing: boolean;
  onPropose: (open: boolean) => void;
  onDone: (message: string) => Promise<void>;
  onStale: () => Promise<void>;
}) {
  const next = formatMonth(closes.next.month);
  const waiting = closes.closes.find((close) => close.status === "proposed") ?? null;
  const past = closes.closes.filter((close) => close.status !== "proposed");
  return (
    <>
      <dl className="m-0 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Fact name="Last month closed" value={closes.lastClosed ? formatMonth(closes.lastClosed) : "None yet"} />
        <Fact name="Next to close" value={next} />
        <Fact name="Days closed through" value={closes.next.closedThrough ? formatDay(closes.next.closedThrough) : "No day yet"} />
      </dl>
      {waiting ? (
        <Waiting close={waiting} me={me} api={api} newKey={newKey} onDone={onDone} onStale={onStale} />
      ) : closes.next.refusal ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
          {refusalText(closes.next.refusal, next, closes.next.closedThrough ? formatDay(closes.next.closedThrough) : null)}
        </p>
      ) : proposing ? (
        <ReasonForm
          label={`Propose closing ${next}`}
          button={`Propose closing ${next}`}
          busy="Proposing"
          hint="A different member of staff approves it. The books are read again then, and the close is refused if they changed."
          month={next}
          onCancel={() => onPropose(false)}
          onSubmit={(reason, key) => api.propose(closes.next.month, reason, key).then(() => onDone(`Closing ${next} is proposed. A different member of staff must approve it.`))}
          newKey={newKey}
          onStale={onStale}
        />
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => onPropose(true)}>Close {next}</Button>
          <span className="text-sm text-muted">Every day in it is closed and the books balance.</span>
        </div>
      )}
      {past.length ? (
        <div className="flex flex-col gap-2">
          <h3 className="m-0 text-xs font-bold tracking-[0.6px] text-muted uppercase">Earlier closes</h3>
          <ul aria-label="Earlier closes" className="m-0 flex list-none flex-col p-0">
            {past.map((close) => (
              <li key={close.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-divider py-2.5 last:border-b-0">
                <span className="text-md font-bold">{formatMonth(close.month)}</span>
                <span className="text-sm text-muted">
                  {close.proposedBy === me ? "You" : (close.proposedByName ?? "A colleague")} proposed
                  {close.decidedAt ? `, ${close.decidedBy === me ? "you" : (close.decidedByName ?? "a colleague")} ${close.status === "approved" ? "approved" : close.status} on ${TIME.format(new Date(close.decidedAt))}` : ""}
                </span>
                <Pill status={close.status} />
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="m-0 text-sm text-muted">No month has been closed yet.</p>
      )}
    </>
  );
}

function Fact({ name, value }: { name: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg bg-surface-sunken px-3.5 py-3">
      <dt className="text-xs font-bold tracking-[0.6px] text-muted uppercase">{name}</dt>
      <dd className="m-0 text-lg font-extrabold">{value}</dd>
    </div>
  );
}

function Pill({ status }: { status: CloseStatus }) {
  return <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold", STATUS[status].tone)}>{STATUS[status].label}</span>;
}

function Waiting({
  close,
  me,
  api,
  newKey,
  onDone,
  onStale,
}: {
  close: MonthClose;
  me: string;
  api: MonthCloseApi;
  newKey: () => string;
  onDone: (message: string) => Promise<void>;
  onStale: () => Promise<void>;
}) {
  const [choice, setChoice] = useState<CloseDecision | null>(null);
  const month = formatMonth(close.month);
  const choices: CloseDecision[] = close.yours ? ["withdraw"] : ["approve", "decline"];
  const currencies = [...new Set(close.books.map((line) => line.currency))];
  const proposer = close.proposedBy === me ? "you" : (close.proposedByName ?? "a colleague");
  return (
    <div aria-label={`${month}, waiting for approval`} role="group" className="flex flex-col gap-3 rounded-lg border border-lime-border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <p className="m-0 text-[17px] font-extrabold">Closing {month}</p>
          <p className="m-0 text-sm text-muted">
            Proposed by {proposer} on {TIME.format(new Date(close.proposedAt))}: “{close.reason}”
          </p>
          <p className="m-0 text-xs text-muted">
            Books fingerprint <span className="font-mono">{close.booksSha256.slice(0, 16)}</span>
          </p>
        </div>
        <Pill status="proposed" />
      </div>
      {currencies.map((currency) => (
        <BooksTable key={currency} currency={currency} close={close} />
      ))}
      {/* While a reason form is open, its own button acts, so the choices step aside. */}
      <div className={cn("flex flex-wrap gap-2", choice ? "hidden" : null)}>
        {choices.map((decision) => (
          <Button key={decision} variant={choice === decision ? "primary" : "secondary"} aria-expanded={choice === decision} onClick={() => setChoice(choice === decision ? null : decision)}>
            {DECISIONS[decision].button}
          </Button>
        ))}
      </div>
      {close.yours ? <p className="m-0 text-sm text-muted">A different member of staff must approve or decline it.</p> : null}
      {choice ? (
        <ReasonForm
          label={`${DECISIONS[choice].button}, ${month}`}
          button={DECISIONS[choice].button}
          busy={DECISIONS[choice].busy}
          hint={choice === "approve" ? "You will be asked to confirm it is you. The books are read again, and the close is refused if they changed." : null}
          month={month}
          onCancel={() => setChoice(null)}
          onSubmit={(reason, key) => api.decide(close.id, choice, reason, key).then(() => onDone(`${month} ${DECISIONS[choice].done}`))}
          newKey={newKey}
          onStale={onStale}
          key={choice}
        />
      ) : null}
    </div>
  );
}

function BooksTable({ currency, close }: { currency: Currency; close: MonthClose }) {
  const lines = close.books.filter((line) => line.currency === currency);
  return (
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={`Books in ${currency}, as proposed`}>
      <table className="w-full min-w-[520px] border-collapse text-sm">
        <caption className="pb-1.5 text-left text-xs font-bold tracking-[0.6px] text-muted uppercase">Books in {currency}, as proposed</caption>
        <thead>
          <tr className="text-left text-2xs font-extrabold tracking-[0.6px] text-muted uppercase">
            <th className="py-1.5 pr-3">Account</th>
            <th className="py-1.5 pr-3 text-right">Opening</th>
            <th className="py-1.5 pr-3 text-right">Debits</th>
            <th className="py-1.5 pr-3 text-right">Credits</th>
            <th className="py-1.5 text-right">Closing</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.code} className="border-t border-divider">
              <td className="py-2 pr-3 font-mono">{line.code}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{formatNumber({ currency, minor: line.opening })}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{formatNumber({ currency, minor: line.debits })}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{formatNumber({ currency, minor: line.credits })}</td>
              <td className="py-2 text-right font-bold tabular-nums">{formatNumber({ currency, minor: line.closing })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReasonForm({
  label,
  button,
  busy,
  hint,
  month,
  onCancel,
  onSubmit,
  newKey,
  onStale,
}: {
  label: string;
  button: string;
  busy: string;
  hint: string | null;
  month: string;
  onCancel: () => void;
  onSubmit: (reason: string, key: string) => Promise<void>;
  newKey: () => string;
  onStale: () => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [state, setState] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const pending = useRef<{ reason: string; key: string } | null>(null);
  const id = `reason-${label.replaceAll(/[^a-z0-9]+/gi, "-").toLowerCase()}`;

  async function submit() {
    const cleaned = reason.trim();
    if (pending.current?.reason !== cleaned) pending.current = { reason: cleaned, key: newKey() };
    setState({ busy: true, error: null });
    try {
      await onSubmit(cleaned, pending.current.key);
    } catch (error) {
      setState({ busy: false, error: monthCloseErrorMessage(error, month) });
      const known = toMonthCloseError(error);
      if (known.code === "not_found" || known.reason === "not_waiting" || known.reason === "close_already_proposed") await onStale();
    }
  }

  return (
    <form
      aria-label={label}
      className="flex max-w-[560px] flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <label htmlFor={id} className="flex flex-col gap-1.5 text-sm font-bold">
        Reason
        <textarea id={id} rows={2} maxLength={500} value={reason} className={INPUT} onChange={(event) => setReason(event.target.value)} />
      </label>
      {hint ? <p className="m-0 text-xs text-muted">{hint}</p> : null}
      {state.error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {state.error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={state.busy || reason.trim() === ""}>
          {state.busy ? busy : button}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
