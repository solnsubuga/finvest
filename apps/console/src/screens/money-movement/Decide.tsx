import { useId, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";

export type Decision = "approve" | "decline" | "withdraw";

interface DecideProps {
  /** The signed-in person proposed it, so they may only withdraw it. */
  mine: boolean;
  /** What approving does, in a sentence the second person reads before pressing. */
  consequence: string;
  busy: boolean;
  error: string | null;
  /** Shown on the approve button. */
  approveLabel?: string;
  onDecide: (decision: Decision, reason: string) => void;
}

/** The second person's approve or decline, or the first person's withdraw, with a reason. */
export function Decide({ mine, consequence, busy, error, approveLabel = "Approve as second person", onDecide }: DecideProps) {
  const id = useId();
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  function send(decision: Decision) {
    if (!reason.trim()) {
      setProblem("Give a reason. It is kept with the decision.");
      return;
    }
    setProblem(null);
    onDecide(decision, reason.trim());
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-reason`}>{mine ? "Why you are withdrawing it" : "Your reason"}</Label>
        <Input id={`${id}-reason`} value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} />
      </div>
      {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}
      {mine ? (
        <>
          <p className="m-0 text-md text-muted">A different person must approve or decline it.</p>
          <Button variant="secondary" disabled={busy} onClick={() => send("withdraw")} className="h-12 self-start px-6">
            {busy ? "Saving" : "Withdraw"}
          </Button>
        </>
      ) : (
        <>
          <p className="m-0 text-md leading-normal">{consequence}</p>
          <div className="flex flex-wrap gap-2.5">
            <Button disabled={busy} onClick={() => send("approve")} className="h-12 flex-[1_1_180px]">
              {busy ? "Saving" : approveLabel}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => send("decline")} className="h-12 flex-[1_1_180px]">
              Decline
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <p role="alert" className="m-0 rounded-md bg-danger-surface px-3.5 py-3 text-md font-semibold text-danger">
      {message}
    </p>
  );
}

export function DoneNote({ message }: { message: string }) {
  return (
    <p role="status" className="m-0 rounded-md bg-success-surface px-3.5 py-3 text-md font-semibold text-success">
      {message}
    </p>
  );
}

export function Empty({ children }: { children: string }) {
  return <p className="m-0 max-w-[640px] rounded-xl border border-border bg-surface p-5 text-md leading-normal text-muted">{children}</p>;
}

export function Failure({ title, message, onRetry }: { title: string; message: string; onRetry: () => void }) {
  return (
    <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
      <h4 className="m-0 text-lg font-extrabold">{title}</h4>
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      <Button variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    </section>
  );
}
