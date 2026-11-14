import { useCallback, useId, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { emptyDirectory, type ClientCard, type Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { formatDay, formatExact, formatMoment } from "@/screens/reconciliation/format.ts";
import { Decide, DoneNote, Empty, ErrorNote, Failure, type Decision } from "../Decide.tsx";
import { useClients, useCommandKeys, useLoad } from "../load.ts";
import { BulkDebits } from "./BulkDebits.tsx";
import {
  BankPayoutsError,
  bankPayoutsErrorMessage,
  type BankPayout,
  type BankPayoutsApi,
  type Batch,
  type BatchItem,
  type Currency,
  type Outcome,
  type SentBankPayout,
} from "./api.ts";

interface BankPayoutsProps {
  api: BankPayoutsApi;
  /** The signed-in member of staff. */
  me: string;
  /** Whether this person holds `bank_payouts.change`: preparing, deciding, the file and settling. */
  canChange: boolean;
  /** Makes the idempotency key for one command. Every try at the same command carries the same key. */
  newKey?: () => string;
  /** Hands the browser the transfer file. */
  save?: (file: Blob, filename: string) => void;
  /** Names the clients behind the IDs. */
  directory?: Directory;
}

type Clients = ReadonlyMap<string, ClientCard>;

/** The client's name and Finvest account number, or the ID until the directory answers. */
function Client({ id, clients }: { id: string; clients: Clients }) {
  const card = clients.get(id);
  if (!card) return <span className="font-mono text-xs">{id}</span>;
  return (
    <span className="flex flex-col">
      <span className="font-semibold">{card.name}</span>
      {card.accountNumber ? <span className="text-xs text-muted tabular-nums">{card.accountNumber}</span> : null}
    </span>
  );
}

type Area = "waiting" | "batches" | "sent" | "bulk";

// Refusals that mean the lists on screen are out of date.
const REFRESH: ReadonlySet<string> = new Set([
  "not_found",
  "refused",
  "payout_not_queued",
  "payout_in_another_batch",
  "batch_already_decided",
  "payout_not_sent",
  "settlement_already_proposed",
  "settlement_already_decided",
  "payout_not_to_settle",
  "settlement_in_bulk",
]);

const CURRENCIES: Currency[] = ["UGX", "USD"];

export function saveBlob(file: Blob, filename: string) {
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Bank payouts: batches two people send, the file for the bank's portal, and settling each one. */
export function BankPayouts({ api, me, canChange, newKey = () => crypto.randomUUID(), save = saveBlob, directory = emptyDirectory }: BankPayoutsProps) {
  const [currency, setCurrency] = useState<Currency>("UGX");
  const [waiting, reloadWaiting] = useLoad(
    useCallback(() => api.listWaiting(currency), [api, currency]),
    bankPayoutsErrorMessage,
  );
  const [batches, reloadBatches] = useLoad(useCallback(() => api.listBatches(), [api]), bankPayoutsErrorMessage);
  const [sent, reloadSent] = useLoad(useCallback(() => api.listSent(), [api]), bankPayoutsErrorMessage);
  const [bulks, reloadBulks] = useLoad(useCallback(() => api.listBulks(), [api]), bankPayoutsErrorMessage, canChange);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ area: Area; id: string; kind: "done" | "failed"; message: string } | null>(null);
  const keys = useCommandKeys(newKey);

  const clients = useClients(directory, [
    ...(waiting.state === "ready" ? waiting.value.map((each) => each.clientId) : []),
    ...(sent.state === "ready" ? sent.value.map((each) => each.payout.clientId) : []),
    ...(batches.state === "ready" ? batches.value.flatMap((each) => (each.items ?? []).map((item) => item.clientId)) : []),
  ]);

  const reloadAll = () => Promise.all([reloadWaiting(), reloadBatches(), reloadSent(), ...(canChange ? [reloadBulks()] : [])]);

  async function run(area: Area, id: string, request: unknown, command: (key: string) => Promise<void>, done: string) {
    setBusy(true);
    setNote(null);
    const key = keys.keyFor(JSON.stringify(request));
    try {
      await command(key);
      keys.settle();
      setNote({ area, id, kind: "done", message: done });
      await reloadAll();
    } catch (error) {
      // Only a definite refusal closes the command. A lost answer reuses the same key next time.
      if (error instanceof BankPayoutsError && error.code !== "unavailable") keys.settle();
      setNote({ area, id, kind: "failed", message: bankPayoutsErrorMessage(error) });
      if (error instanceof BankPayoutsError && REFRESH.has(error.code)) await reloadAll();
    } finally {
      setBusy(false);
    }
  }

  async function download(batch: Batch) {
    setNote(null);
    try {
      save(await api.downloadFile(batch.id), `finvest-bank-payouts-${batch.id}.csv`);
    } catch (error) {
      setNote({ area: "batches", id: batch.id, kind: "failed", message: bankPayoutsErrorMessage(error) });
    }
  }

  const noteFor = (area: Area, id: string) => (note && note.area === area && note.id === id ? note : null);
  // What was done stays on screen after the batch or payout leaves its list.
  const done = (area: Area) => (note?.area === area && note.kind === "done" ? <DoneNote message={note.message} /> : null);

  return (
    <section aria-labelledby="bank-payouts" className="flex flex-col gap-6">
      <div>
        <h2 id="bank-payouts" className="m-0 text-xl font-extrabold">
          Bank payouts
        </h2>
        <p className="m-0 max-w-[760px] text-md text-muted">
          Paid from the trust account through the bank's portal. One person prepares a batch and a different person approves it. Each payout is then
          settled from the trust statement, again by two people.
        </p>
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h3 className="m-0 text-lg font-extrabold">Waiting to be sent</h3>
          <div role="group" aria-label="Currency" className="flex gap-1.5">
            {CURRENCIES.map((each) => (
              <Button
                key={each}
                variant={each === currency ? "primary" : "secondary"}
                aria-pressed={each === currency}
                onClick={() => {
                  setCurrency(each);
                  setNote(null);
                }}
                className="h-9 px-4"
              >
                {each}
              </Button>
            ))}
          </div>
        </div>
        {waiting.state === "loading" ? (
          <p role="status" className="m-0 text-md text-muted">
            Loading bank payouts waiting to be sent
          </p>
        ) : waiting.state === "failed" ? (
          <Failure title="Bank payouts waiting to be sent could not be loaded" message={waiting.message} onRetry={() => void reloadWaiting()} />
        ) : waiting.value.length === 0 ? (
          <Empty>{`No ${currency} bank payouts are waiting. A payout appears here once it is queued and in no batch.`}</Empty>
        ) : (
          <Prepare
            key={currency}
            currency={currency}
            items={waiting.value}
            clients={clients}
            canChange={canChange}
            busy={busy}
            note={noteFor("waiting", currency)}
            onPrepare={(ids, reason) =>
              void run(
                "waiting",
                currency,
                { prepare: ids, currency, reason },
                (key) => api.prepare(key, currency, ids, reason),
                "Batch prepared. A different person must now approve it before anything is sent.",
              )
            }
          />
        )}
      </div>

      <div className="flex flex-col gap-3">
        <h3 className="m-0 text-lg font-extrabold">Batches</h3>
        {done("batches")}
        {batches.state === "loading" ? (
          <p role="status" className="m-0 text-md text-muted">
            Loading batches
          </p>
        ) : batches.state === "failed" ? (
          <Failure title="Batches could not be loaded" message={batches.message} onRetry={() => void reloadBatches()} />
        ) : batches.value.length === 0 ? (
          <Empty>No batch is waiting for a second person or for its file.</Empty>
        ) : (
          <ul aria-label="Batches" className="m-0 flex list-none flex-col gap-3 p-0">
            {batches.value.map((batch) => (
              <li key={batch.id}>
                <BatchCard
                  batch={batch}
                  clients={clients}
                  me={me}
                  canChange={canChange}
                  busy={busy}
                  note={noteFor("batches", batch.id)}
                  onDownload={() => void download(batch)}
                  onDecide={(decision, reason) =>
                    void run(
                      "batches",
                      batch.id,
                      { batch: batch.id, decision, reason },
                      (key) =>
                        decision === "approve"
                          ? api.approve(key, batch.id, reason)
                          : decision === "decline"
                            ? api.decline(key, batch.id, reason)
                            : api.withdraw(batch.id, reason),
                      BATCH_DONE[decision],
                    )
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-col gap-3">
        <div>
          <h3 className="m-0 text-lg font-extrabold">Sent, waiting for the statement</h3>
          <p className="m-0 text-md text-muted">Settle each one from the trust statement once it is imported.</p>
        </div>
        {done("sent")}
        {sent.state === "loading" ? (
          <p role="status" className="m-0 text-md text-muted">
            Loading bank payouts sent
          </p>
        ) : sent.state === "failed" ? (
          <Failure title="Bank payouts sent could not be loaded" message={sent.message} onRetry={() => void reloadSent()} />
        ) : sent.value.length === 0 ? (
          <Empty>Every bank payout sent has been settled.</Empty>
        ) : (
          <SentList
            items={sent.value}
            clients={clients}
            me={me}
            canChange={canChange}
            busy={busy}
            noteFor={(id) => noteFor("sent", id)}
            onPropose={(item, outcome, evidence, reason) =>
              void run(
                "sent",
                item.payout.id,
                { settle: item.payout.id, outcome, evidence, reason },
                (key) =>
                  api.propose(key, item.payout.id, {
                    outcome,
                    ...(outcome === "paid" ? { statementLineId: evidence } : { statementId: evidence }),
                    reason,
                  }),
                "Proposed. A different person must now approve it.",
              )
            }
            onDecide={(item, decision, reason) => {
              const id = item.settlement?.id ?? "";
              void run(
                "sent",
                item.payout.id,
                { settlement: id, decision, reason },
                (key) =>
                  decision === "approve"
                    ? api.approveSettlement(key, id, reason)
                    : decision === "decline"
                      ? api.declineSettlement(id, reason)
                      : api.withdrawSettlement(id, reason),
                SETTLE_DONE[decision],
              );
            }}
          />
        )}
      </div>

      <BulkDebits
        api={api}
        me={me}
        canChange={canChange}
        busy={busy}
        bulks={bulks}
        onRetry={() => void reloadBulks()}
        sent={sent.state === "ready" ? sent.value : null}
        clients={clients}
        noteFor={(id) => noteFor("bulk", id)}
        done={note?.area === "bulk" && note.kind === "done" ? note : null}
        onPropose={(batchId, lineId, payoutIds, reason) =>
          void run(
            "bulk",
            "new",
            { bulk: batchId, lineId, payoutIds, reason },
            (key) => api.proposeBulk(key, batchId, lineId, payoutIds, reason),
            "Proposed. A different person must now approve the bulk debit before its payouts are settled.",
          )
        }
        onDecide={(bulk, decision, reason) =>
          void run(
            "bulk",
            bulk.id,
            { bulkDecision: bulk.id, decision, reason },
            (key) =>
              decision === "approve"
                ? api.approveBulk(key, bulk.id, reason)
                : decision === "decline"
                  ? api.declineBulk(bulk.id, reason)
                  : api.withdrawBulk(bulk.id, reason),
            BULK_DONE[decision],
          )
        }
      />
    </section>
  );
}

const BULK_DONE: Record<Decision, string> = {
  approve: "Approved. Every payout in the bulk debit is settled as paid, and each client has been told.",
  decline: "Declined. Its payouts are waiting to be settled again.",
  withdraw: "Withdrawn. Its payouts are waiting to be settled again.",
};

const BATCH_DONE: Record<Decision, string> = {
  approve: "Approved. The payouts count as sent. Download the file and upload it in the bank's portal.",
  decline: "Declined. Its payouts are waiting to be sent again.",
  withdraw: "Withdrawn. Its payouts are waiting to be sent again.",
};

const SETTLE_DONE: Record<Decision, string> = {
  approve: "Settled.",
  decline: "Declined. The payout is waiting for a new proposal.",
  withdraw: "Withdrawn. The payout is waiting for a new proposal.",
};

type Note = { kind: "done" | "failed"; message: string } | null;

function Notice({ note }: { note: Note }) {
  if (!note) return null;
  return note.kind === "done" ? <DoneNote message={note.message} /> : <ErrorNote message={note.message} />;
}

interface PrepareProps {
  currency: Currency;
  items: readonly BankPayout[];
  clients: Clients;
  canChange: boolean;
  busy: boolean;
  note: Note;
  onPrepare: (ids: string[], reason: string) => void;
}

function Prepare({ currency, items, clients, canChange, busy, note, onPrepare }: PrepareProps) {
  const id = useId();
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const picked = items.filter((each) => chosen.has(each.id));
  const total = { minor: picked.reduce((sum, each) => sum + each.amount.minor, 0), currency };
  const all = picked.length === items.length;

  function toggle(payoutId: string) {
    const next = new Set(chosen);
    if (next.has(payoutId)) next.delete(payoutId);
    else next.add(payoutId);
    setChosen(next);
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="Bank payouts waiting to be sent">
        <table className="w-full min-w-[600px] border-collapse whitespace-nowrap text-md">
          <caption className="sr-only">Bank payouts waiting to be sent, oldest first</caption>
          <thead>
            <tr className="text-left text-xs text-muted">
              {canChange ? (
                <th scope="col" className="w-10 px-3 py-3.5">
                  <input
                    type="checkbox"
                    aria-label="Choose all"
                    checked={all}
                    onChange={() => setChosen(all ? new Set() : new Set(items.map((each) => each.id)))}
                    className="size-4 accent-black"
                  />
                </th>
              ) : null}
              {["Amount", "Client", "Asked for", "Business day", "Payout"].map((heading) => (
                <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="border-t border-divider">
                {canChange ? (
                  <td className="px-3 py-3">
                    <input
                      type="checkbox"
                      aria-label={`Choose ${formatExact(item.amount)}, payout ${item.id}`}
                      checked={chosen.has(item.id)}
                      onChange={() => toggle(item.id)}
                      className="size-4 accent-black"
                    />
                  </td>
                ) : null}
                <th scope="row" className="px-3 py-3 text-left font-bold tabular-nums">
                  {formatExact(item.amount)}
                </th>
                <td className="px-3 py-3">
                  <Client id={item.clientId} clients={clients} />
                </td>
                <td className="px-3 py-3">{formatMoment(item.createdAt)}</td>
                <td className="px-3 py-3">{formatDay(item.businessDate)}</td>
                <td className="px-3 py-3 font-mono text-xs">{item.id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canChange ? (
        <form aria-label="Prepare a batch" className="flex max-w-[640px] flex-col gap-3" onSubmit={(event) => event.preventDefault()}>
          <p className="m-0 text-md font-bold tabular-nums">
            {picked.length === 0 ? "No payouts chosen" : `${picked.length} chosen, ${formatExact(total)}`}
          </p>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-reason`}>Reason for this batch</Label>
            <Input id={`${id}-reason`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
          </div>
          {problem ? <ErrorNote message={problem} /> : <Notice note={note} />}
          <Button
            disabled={busy}
            className="h-12 self-start px-6"
            onClick={() => {
              if (picked.length === 0) {
                setProblem("Choose the payouts to put in the batch.");
                return;
              }
              if (!reason.trim()) {
                setProblem("Give a reason. It is kept with the batch.");
                return;
              }
              setProblem(null);
              onPrepare(
                picked.map((each) => each.id),
                reason.trim(),
              );
            }}
          >
            {busy ? "Saving" : "Prepare batch"}
          </Button>
          <p className="m-0 text-xs text-muted">Nothing is sent on one person's word. A different person approves the batch first.</p>
        </form>
      ) : null}
    </div>
  );
}

interface BatchCardProps {
  batch: Batch;
  clients: Clients;
  me: string;
  canChange: boolean;
  busy: boolean;
  note: Note;
  onDownload: () => void;
  onDecide: (decision: Decision, reason: string) => void;
}

function BatchCard({ batch, clients, me, canChange, busy, note, onDownload, onDecide }: BatchCardProps) {
  const mine = batch.preparedBy === me;
  const count = batch.payoutIds.length;
  return (
    <article aria-label={`Batch of ${formatExact(batch.total)}`} className="flex max-w-[1040px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h4 className="m-0 text-xl font-extrabold tabular-nums">{formatExact(batch.total)}</h4>
          <p className="m-0 text-md text-muted">
            {count === 1 ? "1 payout" : `${count} payouts`}, prepared {formatMoment(batch.preparedAt)} by {mine ? "you" : "a colleague"}
          </p>
        </div>
        <span
          className={cn(
            "rounded-full px-3 py-1 text-xs font-bold",
            batch.status === "approved" ? "bg-success-surface text-success" : "bg-warning-surface text-warning-text",
          )}
        >
          {batch.status === "approved" ? "Approved, sent" : mine ? "Waiting for a second person" : "Needs a second person"}
        </span>
      </header>
      <p className="m-0 text-md">{batch.reason}</p>
      <p className="m-0 text-xs text-muted">
        Batch <span className="font-mono">{batch.id}</span>
      </p>
      {batch.items ? <BatchItems items={batch.items} clients={clients} /> : null}
      {batch.status === "approved" ? (
        <div className="flex flex-col gap-2">
          {batch.decidedAt ? (
            <p className="m-0 text-md text-muted">
              Approved {formatMoment(batch.decidedAt)} by {batch.decidedBy === me ? "you" : "a colleague"}: {batch.decisionReason}
            </p>
          ) : null}
          {note?.kind === "failed" ? <ErrorNote message={note.message} /> : null}
          {canChange ? (
            <>
              <Button variant="secondary" onClick={onDownload} className="h-11 self-start px-5">
                Download transfer file
              </Button>
              <p className="m-0 text-xs text-muted">
                Upload it in the bank's portal. It holds whole account numbers, so delete it once uploaded. Each download is recorded.
              </p>
            </>
          ) : null}
        </div>
      ) : canChange ? (
        <Decide
          mine={mine}
          busy={busy}
          error={note?.kind === "failed" ? note.message : null}
          consequence="Check each payout above: the amount, and that the account holder is the client. Approving counts every payout in the batch as sent and makes the transfer file."
          onDecide={onDecide}
        />
      ) : null}
    </article>
  );
}

interface SentListProps {
  items: readonly SentBankPayout[];
  clients: Clients;
  me: string;
  canChange: boolean;
  busy: boolean;
  noteFor: (payoutId: string) => Note;
  onPropose: (item: SentBankPayout, outcome: Outcome, evidence: string, reason: string) => void;
  onDecide: (item: SentBankPayout, decision: Decision, reason: string) => void;
}

function SentList({ items, clients, me, canChange, busy, noteFor, onPropose, onDecide }: SentListProps) {
  const [selected, setSelected] = useState<string | null>(null);
  const current = items.find((item) => item.payout.id === selected) ?? items[0] ?? null;
  return (
    <div className="flex flex-wrap items-start gap-5">
      <ul aria-label="Bank payouts sent" className="m-0 flex min-w-0 flex-[1_1_240px] list-none flex-col gap-2.5 p-0">
        {items.map((item) => (
          <li key={item.payout.id}>
            <button
              type="button"
              aria-current={item.payout.id === current?.payout.id ? "true" : undefined}
              onClick={() => setSelected(item.payout.id)}
              className={cn(
                "flex w-full cursor-pointer flex-col rounded-lg border bg-surface px-4 py-3.5 text-left text-ink hover:bg-surface-sunken",
                item.payout.id === current?.payout.id ? "border-2 border-black px-[15px] py-[13px]" : "border-border",
              )}
            >
              <span className="text-md font-bold tabular-nums">{formatExact(item.payout.amount)}</span>
              <span className="text-xs">{clients.get(item.payout.clientId)?.name ?? item.payout.clientId}</span>
              <span className="text-xs text-muted">Sent {formatMoment(item.sentAt)}</span>
              <span className={cn("mt-1 text-xs font-bold", item.settlement ? "text-muted" : "text-warning-text")}>
                {item.settlement ? (item.settlement.proposedBy === me ? "Waiting for a second person" : "Needs a second person") : "Needs a proposal"}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <div className="min-w-0 flex-[3_1_480px]">
        {current ? (
          <SettleBank
            key={`${current.payout.id}:${current.settlement?.id ?? "open"}`}
            item={current}
            clients={clients}
            me={me}
            canChange={canChange}
            busy={busy}
            note={noteFor(current.payout.id)}
            onPropose={(outcome, evidence, reason) => onPropose(current, outcome, evidence, reason)}
            onDecide={(decision, reason) => onDecide(current, decision, reason)}
          />
        ) : null}
      </div>
    </div>
  );
}

const OUTCOMES: Record<Outcome, { label: string; evidence: string; hint: string }> = {
  paid: {
    label: "It was paid",
    evidence: "Statement line ID",
    hint: "The trust statement line going out for this payout's amount, on or after the day its batch was approved.",
  },
  failed: {
    label: "The bank refused it",
    evidence: "Statement ID",
    hint: "A trust statement at least two days after the batch was approved. Every day in between must be imported, with no line going out for this amount.",
  },
};

interface SettleBankProps {
  item: SentBankPayout;
  clients: Clients;
  me: string;
  canChange: boolean;
  busy: boolean;
  note: Note;
  onPropose: (outcome: Outcome, evidence: string, reason: string) => void;
  onDecide: (decision: Decision, reason: string) => void;
}

function SettleBank({ item, clients, me, canChange, busy, note, onPropose, onDecide }: SettleBankProps) {
  const id = useId();
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [evidence, setEvidence] = useState("");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const proposal = item.settlement;
  const mine = proposal?.proposedBy === me;

  return (
    <article aria-label={`Bank payout of ${formatExact(item.payout.amount)}`} className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-col gap-1">
        <h4 className="m-0 text-2xl font-extrabold tabular-nums">{formatExact(item.payout.amount)}</h4>
        <p className="m-0 text-md text-muted">
          {clients.get(item.payout.clientId)?.name ?? "A client"}, sent {formatMoment(item.sentAt)} in batch {item.batchId}.
        </p>
        <p className="m-0 text-xs text-muted">
          Payout <span className="font-mono">{item.payout.id}</span>, client <span className="font-mono">{item.payout.clientId}</span>
        </p>
      </header>
      {!canChange ? (
        <p className="m-0 text-md text-muted">Settling a bank payout needs the "Pay and settle bank payouts" permission.</p>
      ) : proposal?.bulkId ? (
        <div className="rounded-lg bg-surface-sunken px-4 py-3 text-md leading-normal">
          <p className="m-0 font-bold">
            {mine ? "You proposed" : "A colleague proposed"} it paid in one debit for its batch, {formatMoment(proposal.proposedAt)}
          </p>
          <p className="m-0">{proposal.reason}</p>
          <p className="m-0 text-xs text-muted">Decide it with the rest of the bulk debit under "One debit for a whole batch".</p>
        </div>
      ) : proposal ? (
        <div className="flex flex-col gap-3">
          <div className="rounded-lg bg-surface-sunken px-4 py-3 text-md leading-normal">
            <p className="m-0 font-bold">
              {mine ? "You proposed" : "A colleague proposed"} "{OUTCOMES[proposal.outcome].label.toLowerCase()}", {formatMoment(proposal.proposedAt)}
            </p>
            <p className="m-0">{proposal.reason}</p>
            <p className="m-0 text-xs text-muted">
              {proposal.outcome === "paid" ? "Statement line" : "Statement"} <span className="font-mono">{proposal.statementLineId ?? proposal.statementId}</span>
            </p>
          </div>
          <Decide
            mine={mine}
            busy={busy}
            error={note?.kind === "failed" ? note.message : null}
            consequence={`Open the cited statement yourself before approving. ${
              proposal.outcome === "paid"
                ? "Approving records the payout as paid from the trust account."
                : "Approving records the payout as failed, and the money goes back to the client's payout pending account."
            }`}
            onDecide={onDecide}
          />
        </div>
      ) : (
        <form aria-label="Propose how it ended" className="flex flex-col gap-3" onSubmit={(event) => event.preventDefault()}>
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-1 p-0 text-lg font-extrabold">What the trust statement shows</legend>
            {(Object.keys(OUTCOMES) as Outcome[]).map((key) => (
              <label
                key={key}
                className={cn(
                  "flex cursor-pointer gap-3 rounded-lg border px-4 py-3",
                  outcome === key ? "border-2 border-black px-[15px] py-[11px]" : "border-border",
                )}
              >
                <input
                  type="radio"
                  name={`${id}-outcome`}
                  value={key}
                  checked={outcome === key}
                  onChange={() => {
                    setOutcome(key);
                    setProblem(null);
                  }}
                  className="mt-1 size-4 shrink-0 accent-black"
                />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-md font-bold">{OUTCOMES[key].label}</span>
                  <span className="text-xs text-muted">{OUTCOMES[key].hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {outcome ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${id}-evidence`}>{OUTCOMES[outcome].evidence}</Label>
              <Input id={`${id}-evidence`} value={evidence} maxLength={20} className="font-mono" onChange={(event) => setEvidence(event.target.value)} />
              <p className="m-0 text-xs text-muted">Copy it from the Reconciliation screen.</p>
            </div>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-why`}>What you checked</Label>
            <Input id={`${id}-why`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
          </div>
          {problem ? <ErrorNote message={problem} /> : note?.kind === "failed" ? <ErrorNote message={note.message} /> : null}
          <Button
            disabled={busy}
            className="h-12 self-start px-6"
            onClick={() => {
              if (!outcome) return setProblem("Choose what the statement shows.");
              if (!evidence.trim()) return setProblem(`Enter the ${OUTCOMES[outcome].evidence.toLowerCase()}.`);
              if (!reason.trim()) return setProblem("Give a reason. It is kept with the decision.");
              setProblem(null);
              onPropose(outcome, evidence.trim(), reason.trim());
            }}
          >
            {busy ? "Saving" : "Propose"}
          </Button>
          <p className="m-0 text-xs text-muted">A different person approves before the payout is settled.</p>
        </form>
      )}
    </article>
  );
}

function BatchItems({ items, clients }: { items: readonly BatchItem[]; clients: Clients }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border" tabIndex={0} role="region" aria-label="Payouts in this batch">
      <table className="w-full min-w-[620px] border-collapse whitespace-nowrap text-md">
        <caption className="sr-only">Payouts in this batch</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {["Amount", "Client", "Account holder", "Bank", "Account", "Payout"].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-2.5 font-bold">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.payoutId} className="border-t border-divider">
              <th scope="row" className="px-3 py-2.5 text-left font-bold tabular-nums">
                {formatExact(item.amount)}
              </th>
              <td className="px-3 py-2.5">
                <Client id={item.clientId} clients={clients} />
              </td>
              <td className="px-3 py-2.5">{item.holderName}</td>
              <td className="px-3 py-2.5">{item.bankName ?? "Unknown bank"}</td>
              <td className="px-3 py-2.5 tabular-nums">{item.maskedAccount}</td>
              <td className="px-3 py-2.5 font-mono text-xs">{item.payoutId}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
