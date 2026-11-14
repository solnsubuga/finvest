import { useCallback, useId, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import { DoneNote, Empty, ErrorNote, Failure } from "@/screens/money-movement/Decide.tsx";
import { useCommandKeys, useLoad } from "@/screens/money-movement/load.ts";
import { formatDay, formatExact, formatMoment } from "../format.ts";
import {
  PotTransfersError,
  potTransfersErrorMessage,
  type Amount,
  type Currency,
  type Pot,
  type PotTransfer,
  type PotTransfersApi,
  type TransferRecord,
} from "./api.ts";

interface PotTransfersProps {
  api: PotTransfersApi;
  me: string;
  /** Whether this person holds `reconciliation.change`: recording and deciding. */
  canChange: boolean;
  newKey?: () => string;
  /** Today in Kampala, `YYYY-MM-DD`, the latest day a transfer can be dated. */
  today?: () => string;
}

const REFRESH: ReadonlySet<string> = new Set(["not_found", "refused", "transfer_already_decided"]);
const MINOR_DIGITS: Record<Currency, number> = { UGX: 0, USD: 2 };
const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

/** Reads "48,000,000" or "1,500.25" in the currency's own decimal places. Null for anything else. */
export function parseAmount(text: string, currency: Currency): Amount | null {
  const plain = text.trim().replaceAll(",", "");
  const digits = MINOR_DIGITS[currency];
  const shape = digits ? new RegExp(`^\\d+(\\.\\d{1,${digits}})?$`) : /^\d+$/;
  if (!shape.test(plain)) return null;
  const [whole = "0", fraction = ""] = plain.split(".");
  const minor = Number(whole) * 10 ** digits + Number(fraction.padEnd(digits, "0") || "0");
  return Number.isSafeInteger(minor) ? { minor, currency } : null;
}

const potKey = (pot: Pick<Pot, "source" | "accountReference">) => `${pot.source}:${pot.accountReference}`;

/** Transfers between Finvest's own pots: one person records what moved, a second approves it. */
export function PotTransfers({ api, me, canChange, newKey = () => crypto.randomUUID(), today = kampalaToday }: PotTransfersProps) {
  const [pots] = useLoad(useCallback(() => api.listPots(), [api]), potTransfersErrorMessage, canChange);
  const [waiting, reload] = useLoad(useCallback(() => api.listWaiting(), [api]), potTransfersErrorMessage);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ id: string; kind: "done" | "failed"; message: string } | null>(null);
  const keys = useCommandKeys(newKey);

  async function run(id: string, request: unknown, command: (key: string) => Promise<void>, done: string) {
    setBusy(true);
    setNote(null);
    const key = keys.keyFor(JSON.stringify(request));
    try {
      await command(key);
      keys.settle();
      setNote({ id, kind: "done", message: done });
      await reload();
    } catch (error) {
      if (error instanceof PotTransfersError && error.code !== "unavailable") keys.settle();
      setNote({ id, kind: "failed", message: potTransfersErrorMessage(error) });
      if (error instanceof PotTransfersError && REFRESH.has(error.code)) await reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="pot-transfers" className="flex flex-col gap-4">
      <div>
        <h2 id="pot-transfers" className="m-0 text-xl font-extrabold">
          Transfers between pots
        </h2>
        <p className="m-0 max-w-[760px] text-md text-muted">
          Finvest's own money moving between its pots, such as a sweep from an ioTec wallet to the trust account. Record it as the provider or bank shows
          it. A different person approves it, which posts it to the books.
        </p>
      </div>
      {note?.kind === "done" ? <DoneNote message={note.message} /> : null}

      {canChange ? (
        pots.state === "ready" ? (
          <RecordTransfer
            pots={pots.value}
            busy={busy}
            today={today()}
            error={note?.id === "new" && note.kind === "failed" ? note.message : null}
            onRecord={(record) =>
              void run("new", record, (key) => api.record(key, record), "Recorded. A different person must now approve it before it is posted.")
            }
          />
        ) : pots.state === "failed" ? (
          <ErrorNote message={pots.message} />
        ) : null
      ) : null}

      <h3 className="m-0 text-lg font-extrabold">Waiting for a second person</h3>
      {waiting.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading transfers between pots
        </p>
      ) : waiting.state === "failed" ? (
        <Failure title="Transfers between pots could not be loaded" message={waiting.message} onRetry={() => void reload()} />
      ) : waiting.value.length === 0 ? (
        <Empty>No transfer between pots is waiting for a second person.</Empty>
      ) : (
        <ul aria-label="Transfers between pots waiting" className="m-0 flex list-none flex-col gap-3 p-0">
          {waiting.value.map((transfer) => (
            <li key={transfer.id}>
              <TransferCard
                transfer={transfer}
                me={me}
                canChange={canChange}
                busy={busy}
                error={note?.id === transfer.id && note.kind === "failed" ? note.message : null}
                onApprove={() => void run(transfer.id, { approve: transfer.id }, (key) => api.approve(key, transfer.id), "Approved and posted to the books.")}
                onDecline={(reason) =>
                  void run(transfer.id, { decline: transfer.id, reason }, (key) => api.decline(key, transfer.id, reason), "Declined. Nothing was posted.")
                }
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function TransferCard({
  transfer,
  me,
  canChange,
  busy,
  error,
  onApprove,
  onDecline,
}: {
  transfer: PotTransfer;
  me: string;
  canChange: boolean;
  busy: boolean;
  error: string | null;
  onApprove: () => void;
  onDecline: (reason: string) => void;
}) {
  const id = useId();
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const mine = transfer.proposedBy === me;
  return (
    <article aria-label={`Transfer of ${formatExact(transfer.sent)} from ${transfer.fromPot.name}`} className="flex max-w-[760px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h4 className="m-0 text-xl font-extrabold">
            {transfer.fromPot.name} to {transfer.toPot.name}
          </h4>
          <p className="m-0 text-md text-muted">
            {formatDay(transfer.valueDate)}, recorded {formatMoment(transfer.proposedAt)} by {mine ? "you" : "a colleague"}
          </p>
        </div>
        <span className={cn("rounded-full px-3 py-1 text-xs font-bold", mine ? "bg-surface-sunken text-muted" : "bg-warning-surface text-warning-text")}>
          {mine ? "Waiting for a second person" : "Needs a second person"}
        </span>
      </header>
      <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-3">
        {[
          ["Sent", formatExact(transfer.sent)],
          ["Arrived", formatExact(transfer.received)],
          ["Provider's fee", formatExact(transfer.fee)],
        ].map(([term, value]) => (
          <div key={term} className="rounded-lg bg-surface-sunken px-3.5 py-2.5">
            <dt className="text-xs text-muted">{term}</dt>
            <dd className="m-0 text-md font-bold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="m-0 text-xs text-muted">
        Reference <span className="font-mono">{transfer.providerReference}</span>, transfer <span className="font-mono">{transfer.id}</span>
      </p>
      {!canChange ? null : mine ? (
        <>
          {error ? <ErrorNote message={error} /> : null}
          <p className="m-0 text-md text-muted">You recorded this, so a different person must approve or decline it.</p>
        </>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="m-0 text-md leading-normal">
            Check both statements before approving. Approving posts it: the receiving pot gains what arrived, the provider's fee is recorded, and the
            sending pot loses what was sent.
          </p>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-reason`}>Reason, if you decline</Label>
            <Input id={`${id}-reason`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
          </div>
          {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}
          <div className="flex flex-wrap gap-2.5">
            <Button
              disabled={busy}
              className="h-12 flex-[1_1_180px]"
              onClick={() => {
                setProblem(null);
                onApprove();
              }}
            >
              {busy ? "Saving" : "Approve as second person"}
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              className="h-12 flex-[1_1_180px]"
              onClick={() => {
                if (!reason.trim()) return setProblem("Give a reason for declining. It is kept with the decision.");
                setProblem(null);
                onDecline(reason.trim());
              }}
            >
              Decline
            </Button>
          </div>
        </div>
      )}
    </article>
  );
}

function RecordTransfer({
  pots,
  busy,
  today,
  error,
  onRecord,
}: {
  pots: readonly Pot[];
  busy: boolean;
  today: string;
  error: string | null;
  onRecord: (record: TransferRecord) => void;
}) {
  const id = useId();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [sent, setSent] = useState("");
  const [received, setReceived] = useState("");
  const [valueDate, setValueDate] = useState(today);
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const fromPot = pots.find((each) => potKey(each) === from) ?? null;
  const toPot = pots.find((each) => potKey(each) === to) ?? null;
  const currency = fromPot?.currency ?? "UGX";
  const sentAmount = parseAmount(sent, currency);
  const receivedAmount = parseAmount(received, currency);
  const fee = sentAmount && receivedAmount ? { minor: sentAmount.minor - receivedAmount.minor, currency } : null;

  function submit() {
    if (!fromPot || !toPot) return setProblem("Choose the pot the money left and the pot it arrived in.");
    if (!sentAmount || sentAmount.minor <= 0) return setProblem("Enter what was sent.");
    if (!receivedAmount || receivedAmount.minor <= 0) return setProblem("Enter what arrived.");
    if (!valueDate) return setProblem("Enter the day the money moved.");
    if (!reference.trim()) return setProblem("Enter the provider's or bank's reference for the transfer.");
    if (!reason.trim()) return setProblem("Give a reason. It is kept with the transfer.");
    setProblem(null);
    onRecord({
      from: { source: fromPot.source, accountReference: fromPot.accountReference },
      to: { source: toPot.source, accountReference: toPot.accountReference },
      sent: sentAmount,
      received: receivedAmount,
      valueDate,
      providerReference: reference.trim(),
      reason: reason.trim(),
    });
  }

  const select = "h-12 w-full rounded-lg border border-border bg-surface px-3.5 text-md";
  return (
    <form aria-label="Record a transfer between pots" className="flex max-w-[760px] flex-col gap-3 rounded-xl border border-border bg-surface p-5" onSubmit={(event) => event.preventDefault()}>
      <h3 className="m-0 text-lg font-extrabold">Record a transfer that was made</h3>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-from`}>From</Label>
          <select id={`${id}-from`} value={from} onChange={(event) => setFrom(event.target.value)} className={select}>
            <option value="">Choose a pot</option>
            {pots.map((each) => (
              <option key={potKey(each)} value={potKey(each)}>
                {each.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-to`}>To</Label>
          <select id={`${id}-to`} value={to} onChange={(event) => setTo(event.target.value)} className={select}>
            <option value="">Choose a pot</option>
            {pots.map((each) => (
              <option key={potKey(each)} value={potKey(each)}>
                {each.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-sent`}>{`Sent (${currency})`}</Label>
          <Input id={`${id}-sent`} inputMode="decimal" value={sent} className="tabular-nums" onChange={(event) => setSent(event.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-received`}>{`Arrived (${currency})`}</Label>
          <Input id={`${id}-received`} inputMode="decimal" value={received} className="tabular-nums" onChange={(event) => setReceived(event.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-day`}>Day the money moved</Label>
          <Input id={`${id}-day`} type="date" max={today} value={valueDate} onChange={(event) => setValueDate(event.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-reference`}>Provider or bank reference</Label>
          <Input id={`${id}-reference`} value={reference} maxLength={100} className="font-mono" onChange={(event) => setReference(event.target.value)} />
        </div>
      </div>
      <p className="m-0 text-md tabular-nums">
        {fee && fee.minor >= 0 ? `Provider's fee: ${formatExact(fee)}` : "The provider's fee is what was sent minus what arrived."}
      </p>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-why`}>Reason</Label>
        <Input id={`${id}-why`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
      </div>
      {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}
      <Button disabled={busy} onClick={submit} className="h-12 self-start px-6">
        {busy ? "Saving" : "Record transfer"}
      </Button>
      <p className="m-0 text-xs text-muted">Nothing is posted on one person's word. A different person approves it first.</p>
    </form>
  );
}
