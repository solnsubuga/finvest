import { useCallback, useId, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import type { ClientCard } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { formatDay, formatExact, formatMoment } from "@/screens/reconciliation/format.ts";
import { Decide, DoneNote, Empty, ErrorNote, Failure, type Decision } from "../Decide.tsx";
import { useLoad, type Load } from "../load.ts";
import { bankPayoutsErrorMessage, type BankPayoutsApi, type Batch, type BulkSettlement, type SentBankPayout } from "./api.ts";

type Clients = ReadonlyMap<string, ClientCard>;
type Note = { kind: "done" | "failed"; message: string } | null;

interface BulkDebitsProps {
  api: BankPayoutsApi;
  me: string;
  canChange: boolean;
  busy: boolean;
  /** Bulk debits waiting for a second person. */
  bulks: Load<readonly BulkSettlement[]>;
  onRetry: () => void;
  /** The sent payouts, to show each one a bulk names. Null until they load. */
  sent: readonly SentBankPayout[] | null;
  /** The batches, to say when each was approved. Null until they load. */
  batches: readonly Batch[] | null;
  clients: Clients;
  /** The note for one bulk, or for "new" while proposing. */
  noteFor: (id: string) => Note;
  done: Note;
  onPropose: (batchId: string, lineId: string, payoutIds: string[], reason: string) => void;
  onDecide: (bulk: BulkSettlement, decision: Decision, reason: string) => void;
}

/**
 * Many payouts of one batch settled from one debit on the trust statement: the bank often takes
 * a whole uploaded file in one line. One person picks the line and the payouts it paid, which
 * must add up to it exactly, and a different person approves. The backend checks every rule.
 */
export function BulkDebits({ api, me, canChange, busy, bulks, onRetry, sent, batches, clients, noteFor, done, onPropose, onDecide }: BulkDebitsProps) {
  if (!canChange) return null;
  // Approved batches with a payout still to settle and no proposal yet: those that can take a debit.
  const open = (sent ?? []).filter((each) => !each.settlement);
  const batchIds = [...new Set(open.map((each) => each.batchId))];
  const choices = batchIds.map((batchId) => {
    const approvedAt = batches?.find((each) => each.id === batchId)?.decidedAt ?? null;
    const sentAt = open.find((each) => each.batchId === batchId)?.sentAt ?? null;
    return { batchId, approvedAt, sentAt, left: open.filter((each) => each.batchId === batchId).length };
  });
  const byId = new Map((sent ?? []).map((each) => [each.payout.id, each]));
  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="m-0 text-lg font-extrabold">One debit for a whole batch</h3>
        <p className="m-0 max-w-[760px] text-md text-muted">
          When the bank took a batch in one line on the trust statement, settle its payouts together from that line. Leave out any payout the bank did
          not pay, and settle it on its own above.
        </p>
      </div>
      {done?.kind === "done" ? <DoneNote message={done.message} /> : null}
      {bulks.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading bulk debits waiting for a second person
        </p>
      ) : bulks.state === "failed" ? (
        <Failure title="Bulk debits could not be loaded" message={bulks.message} onRetry={onRetry} />
      ) : bulks.value.length === 0 ? null : (
        <ul aria-label="Bulk debits waiting for a second person" className="m-0 flex list-none flex-col gap-3 p-0">
          {bulks.value.map((bulk) => (
            <li key={bulk.id}>
              <BulkCard
                bulk={bulk}
                byId={byId}
                loaded={sent !== null}
                clients={clients}
                me={me}
                busy={busy}
                note={noteFor(bulk.id)}
                onDecide={(decision, reason) => onDecide(bulk, decision, reason)}
              />
            </li>
          ))}
        </ul>
      )}
      {sent === null ? null : batchIds.length === 0 ? (
        <Empty>No sent batch has payouts waiting to be settled from a bulk debit.</Empty>
      ) : (
        <ProposeBulk api={api} choices={choices} clients={clients} busy={busy} note={noteFor("new")} onPropose={onPropose} />
      )}
    </div>
  );
}

function BulkCard({
  bulk,
  byId,
  loaded,
  clients,
  me,
  busy,
  note,
  onDecide,
}: {
  bulk: BulkSettlement;
  byId: ReadonlyMap<string, SentBankPayout>;
  /** False until the sent list is in, so a missing payout reads as loading, not as no amount. */
  loaded: boolean;
  clients: Clients;
  me: string;
  busy: boolean;
  note: Note;
  onDecide: (decision: Decision, reason: string) => void;
}) {
  const mine = bulk.proposedBy === me;
  const count = bulk.payoutIds.length;
  return (
    <article aria-label={`Bulk debit of ${formatExact(bulk.total)}`} className="flex max-w-[1040px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h4 className="m-0 text-xl font-extrabold tabular-nums">{formatExact(bulk.total)}</h4>
          <p className="m-0 text-md text-muted">
            {count === 1 ? "1 payout" : `${count} payouts`} from one debit, proposed {formatMoment(bulk.proposedAt)} by {mine ? "you" : "a colleague"}
          </p>
        </div>
        <span className="rounded-full bg-warning-surface px-3 py-1 text-xs font-bold text-warning-text">
          {mine ? "Waiting for a second person" : "Needs a second person"}
        </span>
      </header>
      <p className="m-0 text-md">{bulk.reason}</p>
      <section aria-label="The debit on the trust statement" className="flex flex-col gap-1 rounded-lg border border-border bg-surface-sunken px-4 py-3">
        <h5 className="m-0 text-xs font-bold text-muted">The debit on the trust statement</h5>
        {bulk.line ? (
          <>
            <p className="m-0 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-md">
              <span className="font-bold tabular-nums">{formatExact(bulk.line.amount)}</span>
              <span>{formatDay(bulk.line.valueDate)}</span>
              <span className="font-mono text-xs">{bulk.line.reference}</span>
            </p>
            <p className="m-0 text-md text-muted">{bulk.line.narrative}</p>
          </>
        ) : (
          <p className="m-0 text-md text-muted">The line is no longer in the statement search. Find it on the trust statement by its ID.</p>
        )}
        <p className="m-0 text-xs text-muted">
          Batch <span className="font-mono">{bulk.batchId}</span>, statement line <span className="font-mono">{bulk.statementLineId}</span>
        </p>
      </section>
      <div className="overflow-x-auto rounded-lg border border-border" tabIndex={0} role="region" aria-label="Payouts in this bulk debit">
        <table className="w-full min-w-[480px] border-collapse whitespace-nowrap text-md">
          <caption className="sr-only">Payouts in this bulk debit</caption>
          <thead>
            <tr className="text-left text-xs text-muted">
              {["Amount", "Client", "Payout"].map((heading) => (
                <th key={heading} scope="col" className="px-3 py-2.5 font-bold">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bulk.payoutIds.map((id) => {
              const item = byId.get(id);
              return (
                <tr key={id} className="border-t border-divider">
                  <th scope="row" className="px-3 py-2.5 text-left font-bold tabular-nums">
                    {item ? formatExact(item.payout.amount) : loaded ? "Not in the sent list" : "Loading"}
                  </th>
                  <td className="px-3 py-2.5">{item ? (clients.get(item.payout.clientId)?.name ?? item.payout.clientId) : loaded ? "" : "Loading"}</td>
                  <td className="px-3 py-2.5 font-mono text-xs">{id}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <Decide
        mine={mine}
        busy={busy}
        error={note?.kind === "failed" ? note.message : null}
        consequence="Open the trust statement yourself and check the line is one debit for these payouts. Approving records every payout in it as paid from the trust account, and tells each client."
        onDecide={onDecide}
      />
    </article>
  );
}

function ProposeBulk({
  api,
  choices,
  clients,
  busy,
  note,
  onPropose,
}: {
  api: BankPayoutsApi;
  choices: readonly { batchId: string; approvedAt: string | null; sentAt: string | null; left: number }[];
  clients: Clients;
  busy: boolean;
  note: Note;
  onPropose: (batchId: string, lineId: string, payoutIds: string[], reason: string) => void;
}) {
  const batchIds = choices.map((each) => each.batchId);
  const [batchId, setBatchId] = useState(batchIds[0] ?? "");
  const current = batchIds.includes(batchId) ? batchId : (batchIds[0] ?? "");
  return (
    <form aria-label="Settle a batch from one debit" className="flex max-w-[1040px] flex-col gap-4 rounded-xl border border-border bg-surface p-5" onSubmit={(event) => event.preventDefault()}>
      <h4 className="m-0 text-lg font-extrabold">Settle a batch from one debit</h4>
      {batchIds.length > 1 ? (
        <div role="group" aria-label="Batch" className="flex flex-wrap gap-1.5">
          {choices.map((each) => (
            <Button
              key={each.batchId}
              variant={each.batchId === current ? "primary" : "secondary"}
              aria-pressed={each.batchId === current}
              onClick={() => setBatchId(each.batchId)}
              className="h-auto flex-col items-start gap-0 px-3 py-1.5 text-left"
            >
              <span className="text-md font-bold">
                {each.approvedAt ? `Approved ${formatMoment(each.approvedAt)}` : each.sentAt ? `Sent ${formatMoment(each.sentAt)}` : "Batch"},{" "}
                {each.left === 1 ? "1 payout left" : `${each.left} payouts left`}
              </span>
              <span className="font-mono text-xs opacity-80">{each.batchId}</span>
            </Button>
          ))}
        </div>
      ) : null}
      {/* A fresh form for each batch, and again once the lists change after a decision. */}
      <DebitForm key={`${current}:${batchIds.join(",")}`} api={api} batchId={current} clients={clients} busy={busy} note={note} onPropose={onPropose} />
    </form>
  );
}

function DebitForm({
  api,
  batchId,
  clients,
  busy,
  note,
  onPropose,
}: {
  api: BankPayoutsApi;
  batchId: string;
  clients: Clients;
  busy: boolean;
  note: Note;
  onPropose: (batchId: string, lineId: string, payoutIds: string[], reason: string) => void;
}) {
  const id = useId();
  const [debit, reload] = useLoad(
    useCallback(() => api.getBulkDebit(batchId), [api, batchId]),
    bankPayoutsErrorMessage,
  );
  const [lineId, setLineId] = useState<string | null>(null);
  // Most debits are for the whole batch, so every payout starts chosen.
  const [left, setLeft] = useState<ReadonlySet<string>>(new Set());
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const ready = debit.state === "ready" ? debit.value : null;
  const line = ready?.lines.find((each) => each.id === lineId) ?? null;
  const picked = ready?.payouts.filter((each) => !left.has(each.id)) ?? [];
  const currency = ready?.batch.currency ?? "UGX";
  const total = { minor: picked.reduce((sum, each) => sum + each.amount.minor, 0), currency };
  const difference = line ? line.amount.minor - total.minor : 0;

  function toggle(payoutId: string) {
    const next = new Set(left);
    if (next.has(payoutId)) next.delete(payoutId);
    else next.add(payoutId);
    setLeft(next);
  }

  return (
    <>
      {debit.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading the batch and the trust statement
        </p>
      ) : debit.state === "failed" ? (
        <Failure title="The batch could not be loaded" message={debit.message} onRetry={() => void reload()} />
      ) : ready ? (
        <>
          <p className="m-0 text-md text-muted">
            Batch <span className="font-mono">{ready.batch.id}</span>, approved {ready.batch.decidedAt ? formatMoment(ready.batch.decidedAt) : ""}:{" "}
            {ready.batch.reason}
          </p>
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-1 p-0 text-md font-extrabold">The debit on the trust statement</legend>
            {ready.lines.length === 0 ? (
              <Empty>No line going out of the trust account since this batch was approved could be it. Import the trust statement first.</Empty>
            ) : (
              ready.lines.map((each) => (
                <label
                  key={each.id}
                  className={cn(
                    "flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border px-4 py-3",
                    each.id === lineId ? "border-2 border-black px-[15px] py-[11px]" : "border-border",
                  )}
                >
                  <input
                    type="radio"
                    name={`${id}-line`}
                    checked={each.id === lineId}
                    onChange={() => {
                      setLineId(each.id);
                      setProblem(null);
                    }}
                    className="size-4 shrink-0 accent-black"
                  />
                  <span className="min-w-[140px] text-md font-bold tabular-nums">{formatExact(each.amount)}</span>
                  <span className="text-md">{formatDay(each.valueDate)}</span>
                  <span className="min-w-0 flex-1 text-md">
                    {each.reference} <span className="text-muted">{each.narrative}</span>
                  </span>
                </label>
              ))
            )}
          </fieldset>
          <div className="overflow-x-auto rounded-lg border border-border" tabIndex={0} role="region" aria-label="Payouts the debit paid">
            <table className="w-full min-w-[480px] border-collapse whitespace-nowrap text-md">
              <caption className="sr-only">Payouts of this batch still to settle</caption>
              <thead>
                <tr className="text-left text-xs text-muted">
                  <th scope="col" className="w-10 px-3 py-2.5">
                    <span className="sr-only">Paid in this debit</span>
                  </th>
                  {["Amount", "Client", "Payout"].map((heading) => (
                    <th key={heading} scope="col" className="px-3 py-2.5 font-bold">
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ready.payouts.map((each) => (
                  <tr key={each.id} className="border-t border-divider">
                    <td className="px-3 py-2.5">
                      <input
                        type="checkbox"
                        aria-label={`Paid in this debit: ${formatExact(each.amount)}, payout ${each.id}`}
                        checked={!left.has(each.id)}
                        onChange={() => toggle(each.id)}
                        className="size-4 accent-black"
                      />
                    </td>
                    <th scope="row" className="px-3 py-2.5 text-left font-bold tabular-nums">
                      {formatExact(each.amount)}
                    </th>
                    <td className="px-3 py-2.5">{clients.get(each.clientId)?.name ?? each.clientId}</td>
                    <td className="px-3 py-2.5 font-mono text-xs">{each.id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p
            role="status"
            className={cn("m-0 text-md font-bold tabular-nums", line && difference !== 0 ? "text-warning-text" : line ? "text-success" : "")}
          >
            {`${picked.length} chosen, ${formatExact(total)}. `}
            {!line
              ? "Choose the line."
              : difference === 0
                ? "Adds up to the line exactly."
                : difference > 0
                  ? `The line is ${formatExact({ minor: difference, currency })} more than the payouts chosen.`
                  : `The payouts chosen are ${formatExact({ minor: -difference, currency })} more than the line.`}
          </p>
          <div className="flex max-w-[640px] flex-col gap-1.5">
            <Label htmlFor={`${id}-why`}>What you checked</Label>
            <Input id={`${id}-why`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
          </div>
          {problem ? <ErrorNote message={problem} /> : note?.kind === "failed" ? <ErrorNote message={note.message} /> : null}
          <Button
            disabled={busy}
            className="h-12 self-start px-6"
            onClick={() => {
              if (!line) return setProblem("Choose the line on the trust statement.");
              if (picked.length === 0) return setProblem("Choose the payouts this debit paid.");
              if (difference !== 0) return setProblem("The payouts chosen must add up to the line exactly.");
              if (!reason.trim()) return setProblem("Give a reason. It is kept with the decision.");
              setProblem(null);
              onPropose(
                ready.batch.id,
                line.id,
                picked.map((each) => each.id),
                reason.trim(),
              );
            }}
          >
            {busy ? "Saving" : "Propose"}
          </Button>
          <p className="m-0 text-xs text-muted">A different person approves before any payout is settled.</p>
        </>
      ) : null}
    </>
  );
}
