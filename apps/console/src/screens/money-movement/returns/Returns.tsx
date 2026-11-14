import { useCallback, useId, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { formatDay, formatExact, formatMoment } from "@/screens/reconciliation/format.ts";
import { Decide, DoneNote, Empty, ErrorNote, Failure, type Decision } from "../Decide.tsx";
import { useClients, useCommandKeys, useLoad } from "../load.ts";
import { ReturnsError, returnsErrorMessage, type Amount, type PayoutReturn, type ReturnablePayout, type ReturnsApi } from "./api.ts";

interface ReturnsProps {
  api: ReturnsApi;
  me: string;
  /** Whether this person holds `bank_payouts.change`, which taking back a payout needs. */
  canChange: boolean;
  newKey?: () => string;
  directory?: Directory;
}

const REFRESH: ReadonlySet<string> = new Set(["not_found", "refused", "return_already_decided"]);

const DONE: Record<Decision | "propose", string> = {
  propose: "Proposed. A different person must now approve it.",
  approve: "Taken back. The money is owed to the client again, and they have been told so they can send it again.",
  decline: "Declined. The payout stays paid.",
  withdraw: "Withdrawn. The payout stays paid.",
};

/** Payouts that came back after they were paid: one person names the line, a second approves. */
export function Returns({ api, me, canChange, newKey = () => crypto.randomUUID(), directory = emptyDirectory }: ReturnsProps) {
  const [waiting, reload] = useLoad(useCallback(() => api.listWaiting(), [api]), returnsErrorMessage, canChange);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ id: string; kind: "done" | "failed"; message: string } | null>(null);
  const keys = useCommandKeys(newKey);
  // Counts proposals made, so the form starts afresh, and its lists reload, after each one.
  const [proposals, setProposals] = useState(0);
  const clients = useClients(directory, waiting.state === "ready" ? waiting.value.map((each) => each.clientId) : []);

  async function run(id: string, request: unknown, command: (key: string) => Promise<void>, done: string) {
    setBusy(true);
    setNote(null);
    const key = keys.keyFor(JSON.stringify(request));
    try {
      await command(key);
      keys.settle();
      if (id === "new") setProposals((n) => n + 1);
      setNote({ id, kind: "done", message: done });
      await reload();
    } catch (error) {
      if (error instanceof ReturnsError && error.code !== "unavailable") keys.settle();
      setNote({ id, kind: "failed", message: returnsErrorMessage(error) });
      if (error instanceof ReturnsError && REFRESH.has(error.code)) await reload();
    } finally {
      setBusy(false);
    }
  }

  if (!canChange) return null;
  const items = waiting.state === "ready" ? waiting.value : null;
  const current = items?.find((item) => item.id === selected) ?? items?.[0] ?? null;

  return (
    <section aria-labelledby="payout-returns" className="flex flex-col gap-4">
      <div>
        <h2 id="payout-returns" className="m-0 text-xl font-extrabold">
          Payouts that came back
        </h2>
        <p className="m-0 max-w-[760px] text-md text-muted">
          A paid payout the bank or mobile money network sent back. Pick the payout and the statement line that brought the money back, and a different person approves.
          The client is told, with no amount, and sends it again to an account they choose.
        </p>
      </div>
      {note?.kind === "done" ? <DoneNote message={note.message} /> : null}

      <ProposeReturn
        key={proposals}
        api={api}
        directory={directory}
        busy={busy}
        error={note?.id === "new" && note.kind === "failed" ? note.message : null}
        onPropose={(payoutId, lineId, reason) =>
          void run("new", { payoutId, lineId, reason }, (key) => api.propose(key, payoutId, lineId, reason), DONE.propose)
        }
      />

      {waiting.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading returns waiting for a second person
        </p>
      ) : waiting.state === "failed" ? (
        <Failure title="Returns could not be loaded" message={waiting.message} onRetry={() => void reload()} />
      ) : waiting.value.length === 0 ? (
        <Empty>No return is waiting for a second person.</Empty>
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          <ul aria-label="Returns waiting for a second person" className="m-0 flex min-w-0 flex-[1_1_240px] list-none flex-col gap-2.5 p-0">
            {waiting.value.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  aria-current={item.id === current?.id ? "true" : undefined}
                  onClick={() => {
                    setSelected(item.id);
                    setNote(null);
                  }}
                  className={cn(
                    "flex w-full cursor-pointer flex-col rounded-lg border bg-surface px-4 py-3.5 text-left text-ink hover:bg-surface-sunken",
                    item.id === current?.id ? "border-2 border-black px-[15px] py-[13px]" : "border-border",
                  )}
                >
                  <span className="text-md font-bold">{clients.get(item.clientId)?.name ?? "A client"}</span>
                  <span className="text-md font-bold tabular-nums">{formatExact(item.payout.amount)}</span>
                  <span className="font-mono text-xs">{item.payoutId}</span>
                  <span className="text-xs text-muted">Proposed {formatMoment(item.proposedAt)}</span>
                  <span className="mt-1 text-xs font-bold text-muted">{item.proposedBy === me ? "Waiting for a second person" : "Needs a second person"}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="min-w-0 flex-[3_1_480px]">
            {current ? (
              <ReturnCard
                key={current.id}
                item={current}
                clientName={clients.get(current.clientId)?.name ?? null}
                me={me}
                busy={busy}
                error={note?.id === current.id && note.kind === "failed" ? note.message : null}
                onDecide={(decision, reason) =>
                  void run(
                    current.id,
                    { id: current.id, decision, reason },
                    (key) =>
                      decision === "approve"
                        ? api.approve(key, current.id, reason)
                        : decision === "decline"
                          ? api.decline(current.id, reason)
                          : api.withdraw(current.id, reason),
                    DONE[decision],
                  )
                }
              />
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}

function ReturnCard({
  item,
  clientName,
  me,
  busy,
  error,
  onDecide,
}: {
  item: PayoutReturn;
  clientName: string | null;
  me: string;
  busy: boolean;
  error: string | null;
  onDecide: (decision: Decision, reason: string) => void;
}) {
  const mine = item.proposedBy === me;
  return (
    <article aria-label={`Return of payout ${item.payoutId}`} className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-col gap-1">
        <h3 className="m-0 text-lg font-extrabold">{clientName ? `${clientName}'s payout came back` : "Payout came back"}</h3>
        <p className="m-0 text-xl font-extrabold tabular-nums">{formatExact(item.payout.amount)}</p>
        <p className="m-0 text-md">
          Paid to {whereItWent(item.payout)}, held by {item.payout.holderName}
        </p>
        <p className="m-0 text-xs text-muted">
          Payout <span className="font-mono">{item.payoutId}</span>, client <span className="font-mono">{item.clientId}</span>
        </p>
      </header>
      <section aria-label="The line that brought it back" className="flex flex-col gap-1 rounded-lg border border-border px-4 py-3">
        <h4 className="m-0 text-xs font-bold text-muted">The line that brought it back</h4>
        {item.line ? (
          <>
            <p className="m-0 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-md">
              <span className="font-bold tabular-nums">{formatExact(item.line.amount)}</span>
              <span>{formatDay(item.line.valueDate)}</span>
              <span className="font-mono text-xs">{item.line.reference}</span>
            </p>
            <p className="m-0 text-md text-muted">{item.line.narrative}</p>
          </>
        ) : (
          <p className="m-0 text-md text-muted">This line is not on the statement of the account the payout was paid from. Do not approve until it is checked.</p>
        )}
        <p className="m-0 text-xs text-muted">
          Statement line <span className="font-mono">{item.statementLineId}</span>
        </p>
      </section>
      <div className="rounded-lg bg-surface-sunken px-4 py-3 text-md leading-normal">
        <p className="m-0 font-bold">
          {mine ? "You proposed" : "A colleague proposed"} it, {formatMoment(item.proposedAt)}
        </p>
        <p className="m-0">{item.reason}</p>
      </div>
      <Decide
        mine={mine}
        busy={busy}
        error={error}
        consequence="Open the statement line yourself before approving. Approving takes the money back into the pot and owes it to the client again, and tells them so they can send it again."
        onDecide={onDecide}
      />
    </article>
  );
}

const NETWORKS: Record<string, string> = { mtn: "MTN Mobile Money", airtel: "Airtel Money" };

function whereItWent(payout: Pick<ReturnablePayout, "kind" | "bankName" | "network" | "maskedAccount">): string {
  const place = payout.kind === "bank" ? (payout.bankName ?? "Bank account") : (NETWORKS[payout.network ?? ""] ?? "Mobile money");
  return `${place} ${payout.maskedAccount}`;
}

/** Whole units typed by staff as minor units: UGX has none after the point, USD has cents. */
function parseAmount(text: string, currency: Amount["currency"]): Amount | null | "bad" {
  const cleaned = text.replace(/[,\s]/g, "");
  if (!cleaned) return null;
  const pattern = currency === "UGX" ? /^\d+$/ : /^\d+(\.\d{1,2})?$/;
  if (!pattern.test(cleaned)) return "bad";
  const [whole = "0", part = ""] = cleaned.split(".");
  const minor = currency === "UGX" ? Number(whole) : Number(whole) * 100 + Number(part.padEnd(2, "0"));
  return minor > 0 && Number.isSafeInteger(minor) ? { minor, currency } : "bad";
}

function ProposeReturn({
  api,
  directory,
  busy,
  error,
  onPropose,
}: {
  api: ReturnsApi;
  directory: Directory;
  busy: boolean;
  error: string | null;
  onPropose: (payoutId: string, lineId: string, reason: string) => void;
}) {
  const id = useId();
  const [typed, setTyped] = useState("");
  const [currency, setCurrency] = useState<Amount["currency"]>("UGX");
  const [search, setSearch] = useState<Amount | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [payoutId, setPayoutId] = useState<string | null>(null);
  const [found, reload] = useLoad(
    useCallback(() => api.listReturnable(search ?? undefined), [api, search]),
    returnsErrorMessage,
  );
  const clients = useClients(directory, found.state === "ready" ? found.value.map((each) => each.clientId) : []);
  const chosen = found.state === "ready" ? (found.value.find((each) => each.id === payoutId) ?? null) : null;

  return (
    <form
      aria-label="Record a payout that came back"
      className="flex max-w-[1040px] flex-col gap-4 rounded-xl border border-border bg-surface p-5"
      onSubmit={(event) => {
        event.preventDefault();
        const amount = parseAmount(typed, currency);
        if (amount === "bad") return setProblem(currency === "UGX" ? "Enter whole shillings, such as 2,750,000." : "Enter dollars, such as 1,500.00.");
        setProblem(null);
        setPayoutId(null);
        setSearch(amount);
      }}
    >
      <div>
        <h3 className="m-0 text-lg font-extrabold">Record one that came back</h3>
        <p className="m-0 text-md text-muted">Find the paid payout by the amount that came in, then pick the statement line that brought it back.</p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex min-w-[200px] flex-[0_1_280px] flex-col gap-1.5">
          <Label htmlFor={`${id}-amount`}>Amount that came in</Label>
          <Input id={`${id}-amount`} inputMode="decimal" value={typed} maxLength={20} className="tabular-nums" onChange={(event) => setTyped(event.target.value)} />
        </div>
        <div role="group" aria-label="Currency" className="flex gap-1.5">
          {(["UGX", "USD"] as const).map((each) => (
            <Button key={each} variant={each === currency ? "primary" : "secondary"} aria-pressed={each === currency} onClick={() => setCurrency(each)} className="h-12 px-4">
              {each}
            </Button>
          ))}
        </div>
        <Button type="submit" variant="secondary" className="h-12 px-5">
          Find payouts
        </Button>
        {search ? (
          <Button
            variant="secondary"
            className="h-12 px-5"
            onClick={() => {
              setTyped("");
              setSearch(null);
              setPayoutId(null);
            }}
          >
            Show all
          </Button>
        ) : null}
      </div>
      {problem ? <ErrorNote message={problem} /> : null}
      {found.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading paid payouts
        </p>
      ) : found.state === "failed" ? (
        <Failure title="Paid payouts could not be loaded" message={found.message} onRetry={() => void reload()} />
      ) : found.value.length === 0 ? (
        <Empty>
          {search ? `No payout paid in the last 120 days was for ${formatExact(search)}, or each one already has a return.` : "No payout paid in the last 120 days is free to record as come back."}
        </Empty>
      ) : (
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="mb-1 p-0 text-md font-extrabold">The payout that came back</legend>
          {found.value.map((each) => (
            <label
              key={each.id}
              className={cn(
                "flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border px-4 py-3",
                each.id === payoutId ? "border-2 border-black px-[15px] py-[11px]" : "border-border",
              )}
            >
              <input type="radio" name={`${id}-payout`} checked={each.id === payoutId} onChange={() => setPayoutId(each.id)} className="size-4 shrink-0 accent-black" />
              <span className="min-w-[140px] text-md font-bold tabular-nums">{formatExact(each.amount)}</span>
              <span className="flex min-w-[160px] flex-col">
                <span className="text-md font-semibold">{clients.get(each.clientId)?.name ?? each.holderName}</span>
                <span className="text-xs text-muted">Paid {formatMoment(each.paidAt)}</span>
              </span>
              <span className="min-w-0 flex-1 text-md tabular-nums">{whereItWent(each)}</span>
            </label>
          ))}
        </fieldset>
      )}
      {chosen ? <PickLine key={chosen.id} api={api} payout={chosen} busy={busy} error={error} onPropose={onPropose} /> : null}
    </form>
  );
}

function PickLine({
  api,
  payout,
  busy,
  error,
  onPropose,
}: {
  api: ReturnsApi;
  payout: ReturnablePayout;
  busy: boolean;
  error: string | null;
  onPropose: (payoutId: string, lineId: string, reason: string) => void;
}) {
  const id = useId();
  const [lines, reload] = useLoad(
    useCallback(() => api.listReturnLines(payout.id), [api, payout.id]),
    returnsErrorMessage,
  );
  const [lineId, setLineId] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-3">
      {lines.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading statement lines
        </p>
      ) : lines.state === "failed" ? (
        <Failure title="Statement lines could not be loaded" message={lines.message} onRetry={() => void reload()} />
      ) : lines.value.length === 0 ? (
        <Empty>{`No line coming in for ${formatExact(payout.amount)} on the account it was paid from, since it was paid, is free. Import that statement first.`}</Empty>
      ) : (
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="mb-1 p-0 text-md font-extrabold">The line that brought it back</legend>
          {lines.value.map((each) => (
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
          ))}
        </fieldset>
      )}
      <div className="flex max-w-[640px] flex-col gap-1.5">
        <Label htmlFor={`${id}-why`}>Why it came back</Label>
        <Input id={`${id}-why`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
      </div>
      {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}
      <Button
        disabled={busy}
        className="h-12 self-start px-6"
        onClick={() => {
          if (!lineId) return setProblem("Choose the statement line that brought it back.");
          if (!reason.trim()) return setProblem("Give a reason. It is kept with the return.");
          setProblem(null);
          onPropose(payout.id, lineId, reason.trim());
        }}
      >
        {busy ? "Saving" : "Propose"}
      </Button>
    </div>
  );
}
