import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import { formatDay, formatExact, formatMoment } from "../format.ts";
import {
  REFUSALS,
  ReassignmentError,
  reassignmentErrorMessage,
  type Party,
  type Proposal,
  type ReassignmentDetail,
  type ReassignmentsApi,
  type ReassignmentStatus,
  type ReassignmentSummary,
} from "./api.ts";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

type Command = { action: "approve" } | { action: "send_back"; note: string } | { action: "withdraw" };

interface ReassignmentsProps {
  api: ReassignmentsApi;
  /** Whether the signed-in person may propose, approve, send back and withdraw. Seeing needs less. */
  canChange: boolean;
  /** Makes the idempotency key for one command. Every try at the same command carries the same key. */
  newKey?: () => string;
}

export const STATUS_LABELS: Record<ReassignmentStatus, string> = {
  proposed: "Needs a second person",
  applied: "Moved",
  sent_back: "Sent back",
  withdrawn: "Withdrawn",
};

/**
 * Payments credited to the wrong client: one person proposes moving the money to the client
 * whose money it was, and a different person checks the evidence and approves, which moves it.
 */
export function Reassignments({ api, canChange, newKey = () => crypto.randomUUID() }: ReassignmentsProps) {
  const [params, setParams] = useSearchParams();
  const requestedId = params.get("reassignment");
  const [list, setList] = useState<Load<ReassignmentSummary[]>>({ state: "loading" });
  const [loaded, setLoaded] = useState<{ id: string; load: Load<ReassignmentDetail> } | null>(null);
  const [proposing, setProposing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ id: string; message: string } | null>(null);
  // The move being worked on stays open after a decision, though it is no longer waiting.
  const [kept, setKept] = useState<string | null>(null);
  // The key of a command that has not been answered yet, so a second press replays the first.
  const pending = useRef<{ request: string; key: string } | null>(null);

  const fetchList = useCallback(
    (): Promise<Load<ReassignmentSummary[]>> =>
      api.list().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: reassignmentErrorMessage(error) }),
      ),
    [api],
  );
  const reloadList = async () => setList(await fetchList());

  useEffect(() => {
    let current = true;
    void fetchList().then((load) => current && setList(load));
    return () => {
      current = false;
    };
  }, [fetchList]);

  const items = list.state === "ready" ? list.value : null;
  const selectedId = proposing ? null : (requestedId ?? kept ?? items?.find((item) => item.status === "proposed")?.id ?? items?.[0]?.id ?? null);

  const fetchDetail = useCallback(
    (id: string) =>
      api.get(id).then(
        (value): Load<ReassignmentDetail> => ({ state: "ready", value }),
        (error: unknown): Load<ReassignmentDetail> => ({ state: "failed", message: reassignmentErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    if (!selectedId) return;
    let current = true;
    void fetchDetail(selectedId).then((load) => current && setLoaded({ id: selectedId, load }));
    return () => {
      current = false;
    };
  }, [fetchDetail, selectedId]);

  const detail: Load<ReassignmentDetail> = loaded?.id === selectedId ? loaded.load : { state: "loading" };

  function keyFor(request: string): string {
    if (pending.current?.request === request) return pending.current.key;
    const key = newKey();
    pending.current = { request, key };
    return key;
  }

  function settled(error: unknown) {
    // Only a definite refusal closes the command. A lost answer reuses the key on the next try.
    if (!(error instanceof ReassignmentError) || error.code !== "unavailable") pending.current = null;
  }

  async function run(id: string, command: Command) {
    setBusy(true);
    setFailure(null);
    setKept(id);
    const key = keyFor(JSON.stringify({ id, command }));
    try {
      const updated =
        command.action === "approve"
          ? await api.approve(key, id)
          : command.action === "send_back"
            ? await api.sendBack(key, id, command.note)
            : await api.withdraw(key, id);
      pending.current = null;
      setLoaded({ id, load: { state: "ready", value: updated } });
      await reloadList();
    } catch (error) {
      settled(error);
      setFailure({ id, message: reassignmentErrorMessage(error) });
      if (error instanceof ReassignmentError && error.code !== "unavailable" && error.code !== "confirm_it_is_you") {
        setLoaded({ id, load: await fetchDetail(id) });
        await reloadList();
      }
    } finally {
      setBusy(false);
    }
  }

  async function propose(proposal: Proposal): Promise<ReassignmentError | null> {
    setBusy(true);
    const key = keyFor(JSON.stringify({ propose: proposal }));
    try {
      const made = await api.propose(key, proposal);
      pending.current = null;
      setProposing(false);
      setLoaded({ id: made.id, load: { state: "ready", value: made } });
      setParams({ reassignment: made.id });
      await reloadList();
      return null;
    } catch (error) {
      settled(error);
      return error instanceof ReassignmentError ? error : new ReassignmentError("unavailable");
    } finally {
      setBusy(false);
    }
  }

  const waiting = items?.filter((item) => item.status === "proposed").length ?? 0;
  const summary =
    items === null
      ? "A payment matched to the wrong client is moved to the client whose money it was, by two people."
      : waiting === 0
        ? "Nothing is waiting for a second person."
        : `${waiting} waiting for a second person.`;

  return (
    <section aria-labelledby="reassignments-title" className="flex flex-col gap-4 border-t border-divider pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="reassignments-title" className="m-0 text-xl font-extrabold">
            Payments credited to the wrong client
          </h2>
          <p className="m-0 text-md text-muted">{summary}</p>
        </div>
        {canChange && !proposing ? (
          <Button
            variant="secondary"
            onClick={() => {
              pending.current = null;
              setProposing(true);
            }}
          >
            Propose a move
          </Button>
        ) : null}
      </div>

      {list.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading moves
        </p>
      ) : list.state === "failed" ? (
        <Failure message={list.message} onRetry={() => void reloadList()} />
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          {list.value.length > 0 ? (
            <div className="min-w-0 flex-[1_1_240px]">
              <MoveList
                items={list.value}
                selectedId={selectedId}
                onSelect={(id) => {
                  setKept(null);
                  setProposing(false);
                  pending.current = null;
                  setFailure(null);
                  setParams({ reassignment: id });
                }}
              />
            </div>
          ) : null}
          <div className="min-w-0 flex-[3_1_520px]">
            {proposing ? (
              <ProposeForm busy={busy} onPropose={propose} onCancel={() => setProposing(false)} />
            ) : list.value.length === 0 ? (
              <div className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
                <h3 className="m-0 text-lg font-extrabold">No payments have been moved</h3>
                <p className="m-0 text-md leading-normal text-muted">
                  If a bank transfer was matched to the wrong client, propose moving it here. A second person checks the statement line
                  and approves before any money moves.
                </p>
              </div>
            ) : detail.state === "loading" ? (
              <p role="status" className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
                Loading this move
              </p>
            ) : detail.state === "failed" ? (
              <Failure message={detail.message} />
            ) : (
              <Move
                key={`${detail.value.id}:${detail.value.status}`}
                move={detail.value}
                canChange={canChange}
                busy={busy}
                error={failure?.id === detail.value.id ? failure.message : null}
                onCommand={(command) => void run(detail.value.id, command)}
              />
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function MoveList({
  items,
  selectedId,
  onSelect,
}: {
  items: readonly ReassignmentSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <ul aria-label="Moves" className="m-0 flex list-none flex-col gap-2 p-0">
      {items.map((item) => (
        <li key={item.id}>
          <button
            type="button"
            aria-current={item.id === selectedId ? "true" : undefined}
            onClick={() => onSelect(item.id)}
            className={cn(
              "flex w-full flex-col gap-1 rounded-lg border bg-surface px-4 py-3 text-left",
              item.id === selectedId ? "border-2 border-black px-[15px] py-[11px]" : "border-border",
            )}
          >
            <span className="flex items-center justify-between gap-2">
              <span className="text-md font-extrabold tabular-nums">{formatExact(item.amount)}</span>
              <StatusPill status={item.status} />
            </span>
            <span className="text-xs text-muted">Proposed {formatMoment(item.proposedAt)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function StatusPill({ status }: { status: ReassignmentStatus }) {
  const tone =
    status === "applied"
      ? "bg-success-surface text-success"
      : status === "proposed"
        ? "bg-warning-surface text-warning-text"
        : "bg-surface-sunken text-muted";
  return <span className={cn("shrink-0 rounded-pill px-2.5 py-1 text-xs font-bold", tone)}>{STATUS_LABELS[status]}</span>;
}

function Move({
  move,
  canChange,
  busy,
  error,
  onCommand,
}: {
  move: ReassignmentDetail;
  canChange: boolean;
  busy: boolean;
  error: string | null;
  onCommand: (command: Command) => void;
}) {
  const open = move.status === "proposed";
  return (
    <article aria-label={`Move of ${formatExact(move.amount)}`} className="flex flex-col gap-5 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h3 className="m-0 text-2xl font-extrabold tabular-nums">{formatExact(move.amount)}</h3>
          <p className="m-0 text-md text-muted">Proposed {formatMoment(move.proposedAt)}</p>
        </div>
        <StatusPill status={move.status} />
      </header>

      <div className="flex flex-wrap gap-5">
        <PartyCard title="Credited to" party={move.fromClient} detail={`Investment ${move.fromInvestmentId}`} />
        <PartyCard title="Belongs to" party={move.toClient} detail={`Investment ${move.toInvestmentId}`} />
      </div>

      <Section title="The evidence: the bank statement line">
        {move.line ? (
          <>
            <dl className="m-0">
              <Row name="Amount in" value={formatExact(move.line.amount)} />
              <Row name="Value date" value={formatDay(move.line.valueDate)} />
              <Row name="Bank's description" value={move.line.narrative} />
              <Row name="Reference" value={<span className="tabular-nums">{move.line.reference}</span>} />
              <Row name="Statement" value={move.line.accountReference} />
            </dl>
            <ReferenceHint move={move} />
          </>
        ) : (
          <p className="m-0 py-2.5 text-md text-muted">The statement line cannot be read right now. Reload to try again.</p>
        )}
      </Section>

      <Section title="Why it should move">
        <p className="m-0 py-2.5 text-md leading-normal">{move.reason}</p>
      </Section>

      <Outcome move={move} />
      {open && move.refusal ? (
        <p role="alert" className="m-0 rounded-md bg-warning-surface px-3.5 py-3 text-md leading-normal text-warning-text">
          <strong>This cannot be approved now.</strong> {REFUSALS[move.refusal]}
        </p>
      ) : null}
      {open && !canChange ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
          You can see this move. Approving, sending back and withdrawing need the permission to move payments.
        </p>
      ) : null}
      {open && canChange && move.canDecide ? (
        <DecideForm move={move} busy={busy} error={error} onCommand={onCommand} />
      ) : null}
      {open && canChange && move.yours ? (
        <div className="flex flex-col gap-3 border-t border-divider pt-4">
          <p className="m-0 text-md leading-normal">
            You proposed this, so a second person with the permission to move payments has to approve it. You can withdraw it until
            they do.
          </p>
          {error ? <ErrorNote message={error} /> : null}
          <Button variant="secondary" disabled={busy} onClick={() => onCommand({ action: "withdraw" })} className="h-12 self-start px-6">
            {busy ? "Saving" : "Withdraw the proposal"}
          </Button>
        </div>
      ) : null}
      {!open && error ? <ErrorNote message={error} /> : null}
    </article>
  );
}

function PartyCard({ title, party, detail }: { title: string; party: Party; detail: string }) {
  return (
    <div className="flex min-w-0 flex-[1_1_220px] flex-col gap-1 rounded-lg border border-border px-4 py-3">
      <span className="text-xs font-bold tracking-[0.6px] text-muted uppercase">{title}</span>
      <span className="text-lg font-extrabold">{party.name ?? "Name not on record"}</span>
      <span className="text-md tabular-nums">Account {party.accountNumber ?? "not issued"}</span>
      <span className="truncate text-xs text-muted">{detail}</span>
    </div>
  );
}

function ReferenceHint({ move }: { move: ReassignmentDetail }) {
  const [text, tone] = move.referenceNamesToClient
    ? [`The reference carries ${move.toClient.name ?? "the right client"}'s account number.`, "bg-success-surface text-success"]
    : move.referenceNamesFromClient
      ? [
          `The reference carries ${move.fromClient.name ?? "the first client"}'s account number. Be sure before you move it.`,
          "bg-danger-surface text-danger",
        ]
      : ["The reference carries neither client's account number. Banks often cut it, so check the description and the receipts.", "bg-surface-sunken text-muted"];
  return <p className={cn("m-0 mt-3 rounded-md px-3.5 py-3 text-md leading-normal", tone)}>{text}</p>;
}

function Outcome({ move }: { move: ReassignmentDetail }) {
  let text: ReactNode = null;
  if (move.status === "applied") {
    text = (
      <>
        <strong>Moved {move.decidedAt ? formatMoment(move.decidedAt) : ""}.</strong> The money is now {move.toClient.name ?? "the right client"}'s
        payment for their investment, which starts in a moment. The first client's payment record is unchanged.
      </>
    );
  } else if (move.status === "sent_back") {
    text = (
      <>
        <strong>Sent back {move.decidedAt ? formatMoment(move.decidedAt) : ""}.</strong> {move.decisionNote} Nothing moved.
      </>
    );
  } else if (move.status === "withdrawn") {
    text = "Withdrawn by the person who proposed it. Nothing moved.";
  } else if (move.yours) {
    return null;
  } else if (move.canDecide) {
    text = "Another member of staff proposed this. Check the statement line against both clients before you approve.";
  }
  if (!text) return null;
  return (
    <p role="status" className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
      {text}
    </p>
  );
}

function DecideForm({
  move,
  busy,
  error,
  onCommand,
}: {
  move: ReassignmentDetail;
  busy: boolean;
  error: string | null;
  onCommand: (command: Command) => void;
}) {
  const noteId = useId();
  const [sendingBack, setSendingBack] = useState(false);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  function sendBack() {
    if (!note.trim()) {
      setProblem("Say why, so the person who proposed it knows what to check.");
      return;
    }
    setProblem(null);
    onCommand({ action: "send_back", note: note.trim() });
  }

  return (
    <form aria-label="Decide the move" className="flex flex-col gap-3 border-t border-divider pt-4" onSubmit={(event) => event.preventDefault()}>
      {sendingBack ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={noteId}>Why you are sending it back</Label>
          <textarea
            id={noteId}
            value={note}
            maxLength={500}
            rows={3}
            onChange={(event) => {
              setNote(event.target.value);
              setProblem(null);
            }}
            className="rounded-md border border-border-strong bg-surface px-3 py-2 text-md font-medium"
          />
        </div>
      ) : (
        <p className="m-0 text-md leading-normal">
          Approve only if the statement line is {move.toClient.name ?? "the right client"}'s money. Approving moves{" "}
          {formatExact(move.amount)} from {move.fromClient.name ?? "the first client"} to them at once, and you will be asked to
          confirm it is you.
        </p>
      )}
      {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}
      <div className="flex flex-wrap gap-2.5">
        {sendingBack ? (
          <>
            <Button disabled={busy} onClick={sendBack} className="h-12 flex-[1_1_180px]">
              {busy ? "Saving" : "Send it back"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(false)} className="h-12 flex-[1_1_180px]">
              Go back
            </Button>
          </>
        ) : (
          <>
            <Button disabled={busy || move.refusal !== null} onClick={() => onCommand({ action: "approve" })} className="h-12 flex-[1_1_180px]">
              {busy ? "Saving" : "Approve and move the money"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(true)} className="h-12 flex-[1_1_180px]">
              Send back
            </Button>
          </>
        )}
      </div>
    </form>
  );
}

function ProposeForm({
  busy,
  onPropose,
  onCancel,
}: {
  busy: boolean;
  onPropose: (proposal: Proposal) => Promise<ReassignmentError | null>;
  onCancel: () => void;
}) {
  const ids = useId();
  const [values, setValues] = useState<Proposal>({ depositId: "", toInvestmentId: "", reason: "" });
  const [problems, setProblems] = useState<Partial<Record<keyof Proposal | "form", string | undefined>>>({});

  async function submit(event: FormEvent) {
    event.preventDefault();
    const missing: Partial<Record<keyof Proposal, string>> = {};
    if (!values.depositId.trim()) missing.depositId = "Enter the ID of the payment that was credited to the wrong client.";
    if (!values.toInvestmentId.trim()) missing.toInvestmentId = "Enter the ID of the right client's investment that is waiting for this payment.";
    if (!values.reason.trim()) missing.reason = "Say how you know whose money it is, for the person who checks it.";
    setProblems(missing);
    if (Object.keys(missing).length > 0) return;
    const error = await onPropose({
      depositId: values.depositId.trim(),
      toInvestmentId: values.toInvestmentId.trim(),
      reason: values.reason.trim(),
    });
    if (!error) return;
    const message = reassignmentErrorMessage(error);
    setProblems(error.field && error.field !== "note" ? { [error.field]: message } : { form: message });
  }

  const field = (name: keyof Proposal, label: string, hint: string) => (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={`${ids}-${name}`}>{label}</Label>
      <Input
        id={`${ids}-${name}`}
        value={values[name]}
        aria-invalid={problems[name] ? true : undefined}
        aria-describedby={`${ids}-${name}-hint`}
        onChange={(event) => {
          setValues({ ...values, [name]: event.target.value });
          setProblems({ ...problems, [name]: undefined, form: undefined });
        }}
        className="tabular-nums"
      />
      <p id={`${ids}-${name}-hint`} className={cn("m-0 text-xs", problems[name] ? "font-semibold text-danger" : "text-muted")}>
        {problems[name] ?? hint}
      </p>
    </div>
  );

  return (
    <form aria-label="Propose a move" onSubmit={(event) => void submit(event)} className="flex max-w-[640px] flex-col gap-4 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-col gap-1">
        <h3 className="m-0 text-lg font-extrabold">Propose a move</h3>
        <p className="m-0 text-md leading-normal text-muted">
          For a bank transfer two people confirmed for the wrong client. If that client's investment has started, undo it first on the
          investments screen. Mobile money is never moved: it is refunded to the number it came from.
        </p>
      </div>
      {field("depositId", "Payment credited to the wrong client", "The payment's ID, from the bank transfers list or the client's page.")}
      {field("toInvestmentId", "The right client's investment", "Their investment waiting for exactly this amount, with no payment of its own.")}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-reason`}>How you know whose money it is</Label>
        <textarea
          id={`${ids}-reason`}
          value={values.reason}
          maxLength={500}
          rows={3}
          aria-invalid={problems.reason ? true : undefined}
          onChange={(event) => {
            setValues({ ...values, reason: event.target.value });
            setProblems({ ...problems, reason: undefined, form: undefined });
          }}
          className="rounded-md border border-border-strong bg-surface px-3 py-2 text-md font-medium"
        />
        {problems.reason ? <p className="m-0 text-xs font-semibold text-danger">{problems.reason}</p> : null}
      </div>
      {problems.form ? <ErrorNote message={problems.form} /> : null}
      <div className="flex flex-wrap gap-2.5">
        <Button type="submit" disabled={busy} className="h-12 flex-[1_1_180px]">
          {busy ? "Saving" : "Propose the move"}
        </Button>
        <Button type="button" variant="secondary" disabled={busy} onClick={onCancel} className="h-12 flex-[1_1_180px]">
          Cancel
        </Button>
      </div>
      <p className="m-0 text-xs text-muted">Nothing moves yet. A second person checks the statement line and approves.</p>
    </form>
  );
}

function ErrorNote({ message }: { message: string }) {
  return (
    <p role="alert" className="m-0 rounded-md bg-danger-surface px-3.5 py-3 text-md font-semibold text-danger">
      {message}
    </p>
  );
}

function Failure({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
      <h3 className="m-0 text-lg font-extrabold">Moves could not be loaded</h3>
      <p className="m-0 text-md leading-normal text-muted">{message}</p>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="min-w-0">
      <h4 id={id} className="m-0 text-xs font-bold tracking-[0.6px] text-muted uppercase">
        {title}
      </h4>
      {children}
    </section>
  );
}

function Row({ name, value }: { name: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-divider py-2.5 text-md">
      <dt className="shrink-0 text-muted">{name}</dt>
      <dd className="m-0 text-right font-bold">{value}</dd>
    </div>
  );
}
