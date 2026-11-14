import { useCallback, useId, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { formatMoment } from "@/screens/reconciliation/format.ts";
import { Decide, DoneNote, Empty, ErrorNote, Failure, type Decision } from "../Decide.tsx";
import { useClients, useCommandKeys, useLoad } from "../load.ts";
import { ReturnsError, returnsErrorMessage, type PayoutReturn, type ReturnsApi } from "./api.ts";

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
  const clients = useClients(directory, waiting.state === "ready" ? waiting.value.map((each) => each.clientId) : []);

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
          A paid payout the bank or mobile money network sent back. Name the statement line that brought the money back, and a different person approves.
          The client is told, with no amount, and sends it again to an account they choose.
        </p>
      </div>
      {note?.kind === "done" ? <DoneNote message={note.message} /> : null}

      <ProposeReturn
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
        <p className="m-0 text-xs text-muted">
          Payout <span className="font-mono">{item.payoutId}</span>, client <span className="font-mono">{item.clientId}</span>
        </p>
      </header>
      <div className="rounded-lg bg-surface-sunken px-4 py-3 text-md leading-normal">
        <p className="m-0 font-bold">
          {mine ? "You proposed" : "A colleague proposed"} it, {formatMoment(item.proposedAt)}
        </p>
        <p className="m-0">{item.reason}</p>
        <p className="m-0 text-xs text-muted">
          Statement line <span className="font-mono">{item.statementLineId}</span>
        </p>
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

function ProposeReturn({ busy, error, onPropose }: { busy: boolean; error: string | null; onPropose: (payoutId: string, lineId: string, reason: string) => void }) {
  const id = useId();
  const [payoutId, setPayoutId] = useState("");
  const [lineId, setLineId] = useState("");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      aria-label="Record a payout that came back"
      className="flex max-w-[640px] flex-col gap-3 rounded-xl border border-border bg-surface p-5"
      onSubmit={(event) => event.preventDefault()}
    >
      <h3 className="m-0 text-lg font-extrabold">Record one that came back</h3>
      <div className="flex flex-wrap gap-3">
        <div className="flex min-w-[200px] flex-1 flex-col gap-1.5">
          <Label htmlFor={`${id}-payout`}>Payout ID</Label>
          <Input id={`${id}-payout`} value={payoutId} maxLength={20} className="font-mono" onChange={(event) => setPayoutId(event.target.value)} />
        </div>
        <div className="flex min-w-[200px] flex-1 flex-col gap-1.5">
          <Label htmlFor={`${id}-line`}>Statement line ID</Label>
          <Input id={`${id}-line`} value={lineId} maxLength={20} className="font-mono" onChange={(event) => setLineId(event.target.value)} />
        </div>
      </div>
      <p className="m-0 text-xs text-muted">
        The line coming in on the account the payout was paid from, for its amount, on or after the day it was paid. Copy it from the Reconciliation screen.
      </p>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-why`}>Why it came back</Label>
        <Input id={`${id}-why`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
      </div>
      {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}
      <Button
        disabled={busy}
        className="h-12 self-start px-6"
        onClick={() => {
          if (!payoutId.trim()) return setProblem("Enter the payout ID.");
          if (!lineId.trim()) return setProblem("Enter the statement line ID.");
          if (!reason.trim()) return setProblem("Give a reason. It is kept with the return.");
          setProblem(null);
          onPropose(payoutId.trim(), lineId.trim(), reason.trim());
        }}
      >
        {busy ? "Saving" : "Propose"}
      </Button>
    </form>
  );
}
