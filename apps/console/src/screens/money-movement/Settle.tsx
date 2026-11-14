import { useId, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import { formatExact, formatMoment } from "@/screens/reconciliation/format.ts";
import type { Outcome, StrandedPayout } from "./api.ts";

export type SettleCommand =
  | { action: "propose"; outcome: Outcome; evidence: string; reason: string }
  | { action: "approve"; reason: string }
  | { action: "decline"; reason: string }
  | { action: "withdraw"; reason: string };

interface SettleProps {
  payout: StrandedPayout;
  /** The signed-in member of staff, to tell their own proposal from someone else's. */
  me: string;
  busy: boolean;
  error: string | null;
  onCommand: (command: SettleCommand) => void;
}

const OUTCOMES: Record<Outcome, { label: string; evidence: string; hint: string }> = {
  paid: {
    label: "It was paid",
    evidence: "Statement line ID",
    hint: "The line on the ioTec statement for this payout's wallet that carries its ID, going out, for its amount.",
  },
  failed: {
    label: "It never left",
    evidence: "Statement ID",
    hint: "The wallet's statement for a day at least three days after the send. Every day in between must be imported, with no line carrying this payout's ID.",
  },
};

/** One payout ioTec never answered for: the proposal that settles it, or the decision on one. */
export function Settle({ payout, me, busy, error, onCommand }: SettleProps) {
  const ids = useId();
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [evidence, setEvidence] = useState("");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const proposal = payout.resolution;
  const mine = proposal?.proposedBy === me;

  function send(command: SettleCommand) {
    if (!command.reason.trim()) {
      setProblem("Give a reason. It is kept with the decision.");
      return;
    }
    setProblem(null);
    onCommand(command);
  }

  return (
    <article aria-label={`Payout of ${formatExact(payout.amount)}`} className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-col gap-1">
        <h3 className="m-0 text-2xl font-extrabold tabular-nums">{formatExact(payout.amount)}</h3>
        <p className="m-0 text-md text-muted">
          First sent {formatMoment(payout.firstSentAt)}.{" "}
          {payout.status === "uncertain" ? "ioTec never said whether it took it." : "ioTec took it and never said how it ended."}
        </p>
        <p className="m-0 text-xs text-muted">
          Payout <span className="font-mono">{payout.id}</span>, client <span className="font-mono">{payout.clientId}</span>
        </p>
      </header>

      {proposal ? (
        <div className="flex flex-col gap-3">
          <div className="rounded-lg bg-surface-sunken px-4 py-3 text-md leading-normal">
            <p className="m-0 font-bold">
              {mine ? "You proposed" : "A colleague proposed"} "{OUTCOMES[proposal.outcome].label.toLowerCase()}",{" "}
              {formatMoment(proposal.proposedAt)}
            </p>
            <p className="m-0">{proposal.reason}</p>
            <p className="m-0 text-xs text-muted">
              {proposal.outcome === "paid" ? "Statement line" : "Statement"}{" "}
              <span className="font-mono">{proposal.statementLineId ?? proposal.statementId}</span>
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${ids}-reason`}>{mine ? "Why you are withdrawing it" : "Your reason"}</Label>
            <Input id={`${ids}-reason`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
          </div>
          {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}
          {mine ? (
            <>
              <p className="m-0 text-md text-muted">A different person must approve or decline it.</p>
              <Button variant="secondary" disabled={busy} onClick={() => send({ action: "withdraw", reason })} className="h-12 self-start px-6">
                {busy ? "Saving" : "Withdraw my proposal"}
              </Button>
            </>
          ) : (
            <>
              <p className="m-0 text-md leading-normal">
                Open the cited statement yourself before approving.{" "}
                {proposal.outcome === "paid"
                  ? "Approving records the payout as paid from the wallet."
                  : "Approving records the payout as failed, and the money goes back to the client's payout pending account."}
              </p>
              <div className="flex flex-wrap gap-2.5">
                <Button disabled={busy} onClick={() => send({ action: "approve", reason })} className="h-12 flex-[1_1_180px]">
                  {busy ? "Saving" : "Approve as second person"}
                </Button>
                <Button variant="secondary" disabled={busy} onClick={() => send({ action: "decline", reason })} className="h-12 flex-[1_1_180px]">
                  Decline
                </Button>
              </div>
            </>
          )}
        </div>
      ) : (
        <form aria-label="Propose how it ended" className="flex flex-col gap-3" onSubmit={(event) => event.preventDefault()}>
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-1 p-0 text-lg font-extrabold">What the ioTec statement shows</legend>
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
                  name={`${ids}-outcome`}
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
              <Label htmlFor={`${ids}-evidence`}>{OUTCOMES[outcome].evidence}</Label>
              <Input
                id={`${ids}-evidence`}
                value={evidence}
                maxLength={20}
                className="font-mono"
                onChange={(event) => setEvidence(event.target.value)}
              />
              <p className="m-0 text-xs text-muted">Copy it from the Reconciliation screen.</p>
            </div>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${ids}-why`}>What you checked</Label>
            <Input id={`${ids}-why`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
          </div>
          {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}
          <Button
            disabled={busy}
            onClick={() => {
              if (!outcome) {
                setProblem("Choose what the statement shows.");
                return;
              }
              if (!evidence.trim()) {
                setProblem(`Enter the ${OUTCOMES[outcome].evidence.toLowerCase()}.`);
                return;
              }
              send({ action: "propose", outcome, evidence: evidence.trim(), reason });
            }}
            className="h-12 self-start px-6"
          >
            {busy ? "Saving" : "Propose"}
          </Button>
          <p className="m-0 text-xs text-muted">Nothing moves on one person's word. A different person approves before the payout is settled.</p>
        </form>
      )}
    </article>
  );
}

function ErrorNote({ message }: { message: string }) {
  return (
    <p role="alert" className="m-0 rounded-md bg-danger-surface px-3.5 py-3 text-md font-semibold text-danger">
      {message}
    </p>
  );
}
