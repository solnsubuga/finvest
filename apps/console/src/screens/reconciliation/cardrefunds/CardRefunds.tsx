import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import { formatExact, formatMoment } from "../format.ts";
import {
  REFUSALS,
  RefundError,
  refundErrorMessage,
  type CardRefundsApi,
  type Found,
  type Proposal,
  type RefundDetail,
  type RefundStatus,
  type RefundSummary,
} from "./api.ts";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

type Command =
  | { action: "approve" }
  | { action: "send_back"; note: string }
  | { action: "withdraw" }
  | { action: "finding"; found: Found; note: string };

interface CardRefundsProps {
  api: CardRefundsApi;
  /** Whether the signed-in person may propose, decide and say what Pesapal shows. Seeing needs less. */
  canChange: boolean;
  /** Makes the idempotency key for one command. Every try at the same command carries the same key. */
  newKey?: () => string;
}

const PARAM = "card_refund";

export const STATUS_LABELS: Record<RefundStatus, string> = {
  proposed: "Needs a second person",
  approved: "Approved, being sent",
  sending: "Being sent to Pesapal",
  requested: "Waiting for Pesapal to pay",
  uncertain: "Pesapal's answer was lost",
  done: "Paid back",
  failed: "Failed, money put back",
  sent_back: "Sent back",
  withdrawn: "Withdrawn",
};

/** Why a refund failed, worded for staff. In every case the money waits for the investment again. */
const FAILURES: Record<string, string> = {
  provider_refused: "Pesapal refused the refund request.",
  provider_unavailable: "Pesapal could not be reached, so the request never left.",
  not_paid_by_provider: "Staff found that Pesapal did not pay it.",
  insufficient_funds: "The money was no longer all there when the worker reached it.",
  money_moved_on: "The money stopped waiting for the investment before the worker reached it, most likely because the investment started.",
  already_taken_back: "The payment had already been taken back.",
};

const FOUND_LABELS: Record<Found, { label: string; hint: string }> = {
  paid: { label: "Pesapal paid it", hint: "The refund shows as paid or reversed on Pesapal's dashboard or statement." },
  requested: { label: "Pesapal has the request", hint: "Pesapal shows the refund request, not paid yet. Finvest keeps waiting." },
  not_paid: { label: "Pesapal did not pay it", hint: "Pesapal has no such refund or turned it down. The money waits for the investment again." },
};

/**
 * Card payments paid back to the card through Pesapal. One person proposes, a second approves,
 * and the worker asks Pesapal once. A refund Pesapal took waits until Pesapal shows it paid, or
 * until a second person says what Pesapal shows.
 */
export function CardRefunds({ api, canChange, newKey = () => crypto.randomUUID() }: CardRefundsProps) {
  const [params, setParams] = useSearchParams();
  const requested = params.get(PARAM);
  const [list, setList] = useState<Load<RefundSummary[]>>({ state: "loading" });
  const [loaded, setLoadedAll] = useState<Record<string, Load<RefundDetail>>>({});
  const setLoaded = (id: string, load: Load<RefundDetail>) => setLoadedAll((known) => ({ ...known, [id]: load }));
  const [proposing, setProposing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ id: string; message: string } | null>(null);
  // The refund being worked on stays open after a decision, though it is no longer waiting.
  const [kept, setKept] = useState<string | null>(null);
  // The key of a command that has not been answered yet, so a second press replays the first.
  const pending = useRef<{ request: string; key: string } | null>(null);

  const fetchList = useCallback(
    (): Promise<Load<RefundSummary[]>> =>
      api.list().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: refundErrorMessage(error) }),
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
  const first = items?.find((item) => item.status === "proposed") ?? items?.[0];
  const selected = proposing ? null : (requested ?? kept ?? first?.id ?? null);

  const fetchDetail = useCallback(
    (id: string) =>
      api.get(id).then(
        (value): Load<RefundDetail> => ({ state: "ready", value }),
        (error: unknown): Load<RefundDetail> => ({ state: "failed", message: refundErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    if (!selected) return;
    let current = true;
    void fetchDetail(selected).then((load) => current && setLoaded(selected, load));
    return () => {
      current = false;
    };
  }, [fetchDetail, selected]);

  const detail: Load<RefundDetail> = (selected && loaded[selected]) || { state: "loading" };

  function keyFor(request: string): string {
    if (pending.current?.request === request) return pending.current.key;
    const key = newKey();
    pending.current = { request, key };
    return key;
  }

  function settled(error: unknown) {
    // Only a definite refusal closes the command. A lost answer reuses the key on the next try.
    if (!(error instanceof RefundError) || error.code !== "unavailable") pending.current = null;
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
            : command.action === "withdraw"
              ? await api.withdraw(key, id)
              : await api.recordFinding(key, id, command.found, command.note);
      pending.current = null;
      setLoaded(id, { state: "ready", value: updated });
      await reloadList();
    } catch (error) {
      settled(error);
      setFailure({ id, message: refundErrorMessage(error) });
      if (error instanceof RefundError && error.code !== "unavailable" && error.code !== "confirm_it_is_you") {
        setLoaded(id, await fetchDetail(id));
        await reloadList();
      }
    } finally {
      setBusy(false);
    }
  }

  async function propose(proposal: Proposal): Promise<RefundError | null> {
    setBusy(true);
    const key = keyFor(JSON.stringify({ propose: proposal }));
    try {
      const made = await api.propose(key, proposal);
      pending.current = null;
      setProposing(false);
      setKept(made.id);
      setLoaded(made.id, { state: "ready", value: made });
      setParams({ [PARAM]: made.id });
      await reloadList();
      return null;
    } catch (error) {
      settled(error);
      return error instanceof RefundError ? error : new RefundError("unavailable");
    } finally {
      setBusy(false);
    }
  }

  const waiting = items?.filter((item) => item.status === "proposed").length ?? 0;
  const atPesapal = items?.filter((item) => item.status === "requested" || item.status === "uncertain").length ?? 0;
  const summary =
    items === null
      ? "A card payment is paid back to the same card through Pesapal, by two people."
      : waiting + atPesapal === 0
        ? "Nothing is waiting."
        : [waiting ? `${waiting} waiting for a second person` : null, atPesapal ? `${atPesapal} waiting on Pesapal` : null]
            .filter(Boolean)
            .join(", ") + ".";

  return (
    <section aria-labelledby="card-refunds-title" className="flex flex-col gap-4 border-t border-divider pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="card-refunds-title" className="m-0 text-xl font-extrabold">
            Card payments to pay back
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
            Propose a card refund
          </Button>
        ) : null}
      </div>

      {list.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading card refunds
        </p>
      ) : list.state === "failed" ? (
        <Failure message={list.message} onRetry={() => void reloadList()} />
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          {list.value.length > 0 ? (
            <div className="min-w-0 flex-[1_1_240px]">
              <RefundList
                items={list.value}
                selected={selected}
                onSelect={(id) => {
                  setKept(null);
                  setProposing(false);
                  pending.current = null;
                  setFailure(null);
                  setParams({ [PARAM]: id });
                }}
              />
            </div>
          ) : null}
          <div className="min-w-0 flex-[3_1_520px]">
            {proposing ? (
              <ProposeForm busy={busy} onPropose={propose} onCancel={() => setProposing(false)} />
            ) : list.value.length === 0 ? (
              <div className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
                <h3 className="m-0 text-lg font-extrabold">No card refunds</h3>
                <p className="m-0 text-md leading-normal text-muted">
                  When a client paid by card and should have the money back before their investment starts, propose a refund here. A
                  second person approves it, then Pesapal pays it back to the same card.
                </p>
              </div>
            ) : detail.state === "loading" ? (
              <p role="status" className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
                Loading this refund
              </p>
            ) : detail.state === "failed" ? (
              <Failure message={detail.message} />
            ) : (
              <Refund
                key={`${detail.value.id}:${detail.value.status}:${detail.value.found ?? ""}`}
                item={detail.value}
                canChange={canChange}
                busy={busy}
                error={failure && failure.id === selected ? failure.message : null}
                onCommand={(command) => selected && void run(selected, command)}
              />
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function RefundList({ items, selected, onSelect }: { items: readonly RefundSummary[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <ul aria-label="Card refunds" className="m-0 flex list-none flex-col gap-2 p-0">
      {items.map((item) => {
        const current = item.id === selected;
        return (
          <li key={item.id}>
            <button
              type="button"
              aria-current={current ? "true" : undefined}
              onClick={() => onSelect(item.id)}
              className={cn(
                "flex w-full flex-col gap-1 rounded-lg border bg-surface px-4 py-3 text-left",
                current ? "border-2 border-black px-[15px] py-[11px]" : "border-border",
              )}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="text-md font-extrabold tabular-nums">{formatExact(item.total)}</span>
                <StatusPill status={item.status} />
              </span>
              <span className="text-sm font-semibold">{item.finvestErred ? "With the card charge, Finvest's mistake" : "The investment's amount"}</span>
              <span className="text-xs text-muted">Proposed {formatMoment(item.proposedAt)}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function StatusPill({ status }: { status: RefundStatus }) {
  const tone =
    status === "done"
      ? "bg-success-surface text-success"
      : status === "proposed" || status === "approved" || status === "sending" || status === "requested"
        ? "bg-warning-surface text-warning-text"
        : status === "failed" || status === "uncertain"
          ? "bg-danger-surface text-danger"
          : "bg-surface-sunken text-muted";
  return <span className={cn("shrink-0 rounded-pill px-2.5 py-1 text-xs font-bold", tone)}>{STATUS_LABELS[status]}</span>;
}

function cardWords(item: RefundDetail): string {
  if (item.cardBrand && item.cardLastFour) return `${item.cardBrand} ending ${item.cardLastFour}`;
  if (item.cardLastFour) return `Card ending ${item.cardLastFour}`;
  return item.cardBrand ?? "Card";
}

function Refund({
  item,
  canChange,
  busy,
  error,
  onCommand,
}: {
  item: RefundDetail;
  canChange: boolean;
  busy: boolean;
  error: string | null;
  onCommand: (command: Command) => void;
}) {
  const open = item.status === "proposed";
  return (
    <article aria-label={`Card refund, ${formatExact(item.total)}`} className="flex flex-col gap-5 rounded-xl border border-border bg-surface p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-xs font-bold tracking-[0.6px] text-muted uppercase">Back to the card</span>
          <h3 className="m-0 text-2xl font-extrabold tabular-nums">{formatExact(item.total)}</h3>
          <p className="m-0 text-md text-muted">Proposed {formatMoment(item.proposedAt)}</p>
        </div>
        <StatusPill status={item.status} />
      </header>

      <Section title="What goes back">
        <dl className="m-0">
          <Row name="The investment's amount" value={formatExact(item.amount)} />
          <Row
            name="Card charge the client paid"
            value={item.finvestErred ? formatExact(item.charge) : <span className="text-muted">{formatExact(item.charge)}, kept</span>}
          />
          <Row name="Back to the card" value={formatExact(item.total)} />
        </dl>
        <p className="m-0 pt-2 text-xs leading-normal text-muted">
          {item.finvestErred
            ? "Marked as Finvest's own mistake, so the card charge goes back too."
            : "Not Finvest's mistake, so the card charge stays Finvest's income."}
        </p>
      </Section>

      <Section title="The payment">
        <dl className="m-0">
          <Row name="Payment" value={<span className="tabular-nums">{item.depositId}</span>} />
          <Row name="Card" value={cardWords(item)} />
          <Row name="Client" value={<span className="tabular-nums">{item.clientId}</span>} />
          <Row name="Investment" value={<span className="tabular-nums">{item.investmentId}</span>} />
          {open ? <Row name="Waiting for the investment now" value={formatExact(item.waiting)} /> : null}
        </dl>
      </Section>

      <Section title="Why">
        <p className="m-0 py-2.5 text-md leading-normal">{item.reason}</p>
      </Section>

      <Outcome item={item} />
      {open && item.refusal ? (
        <p role="alert" className="m-0 rounded-md bg-warning-surface px-3.5 py-3 text-md leading-normal text-warning-text">
          <strong>This cannot be approved now.</strong> {REFUSALS[item.refusal]}
        </p>
      ) : null}
      {(open || item.canRecordFinding) && !canChange ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
          You can see this refund. Deciding it and saying what Pesapal shows need the permission to put payments right.
        </p>
      ) : null}
      {open && canChange && item.canDecide ? <DecideForm item={item} busy={busy} error={error} onCommand={onCommand} /> : null}
      {open && canChange && item.yours ? (
        <div className="flex flex-col gap-3 border-t border-divider pt-4">
          <p className="m-0 text-md leading-normal">
            You proposed this, so a second person with the permission to put payments right has to approve it. You can withdraw it until
            they do.
          </p>
          {error ? <ErrorNote message={error} /> : null}
          <Button variant="secondary" disabled={busy} onClick={() => onCommand({ action: "withdraw" })} className="h-12 self-start px-6">
            {busy ? "Saving" : "Withdraw the proposal"}
          </Button>
        </div>
      ) : null}
      {canChange && item.canRecordFinding ? <FindingForm item={item} busy={busy} error={error} onCommand={onCommand} /> : null}
      {!open && !item.canRecordFinding && error ? <ErrorNote message={error} /> : null}
    </article>
  );
}

function Outcome({ item }: { item: RefundDetail }) {
  const when = (moment: string | null) => (moment ? ` ${formatMoment(moment)}` : "");
  const seen = item.found && item.foundNote ? ` Staff saw it there: ${item.foundNote}` : "";
  let text: ReactNode = null;
  switch (item.status) {
    case "done":
      text = (
        <>
          <strong>Paid back{when(item.doneAt)}.</strong> {formatExact(item.total)} went back to the card, and the payment no longer counts for
          the investment.{seen}
        </>
      );
      break;
    case "approved":
    case "sending":
      text = (
        <>
          <strong>Approved{when(item.decidedAt)}.</strong> The worker is holding the money and asking Pesapal now. Reload in a moment.
        </>
      );
      break;
    case "requested":
      text = (
        <>
          <strong>Pesapal took the request{when(item.sentAt)}.</strong> The money is held for the refund, and the client was told it is on its
          way back. Pesapal still has to pay it. Finvest checks every 30 minutes and marks it paid once Pesapal shows it reversed.
          {item.found === "requested" ? ` Staff saw the request on Pesapal${when(item.foundAt)}: ${item.foundNote ?? ""}` : ""}
        </>
      );
      break;
    case "uncertain":
      text = (
        <>
          <strong>Pesapal's answer was lost.</strong> The money is held. The request is never sent again, because Pesapal takes one refund per
          payment. Look the payment up on Pesapal's dashboard and say what it shows.
        </>
      );
      break;
    case "failed":
      text = (
        <>
          <strong>Failed. The money waits for the investment again.</strong>{" "}
          {FAILURES[item.failureCode ?? ""] ?? "The refund could not go ahead."} You can propose it again.
        </>
      );
      break;
    case "sent_back":
      text = (
        <>
          <strong>Sent back{when(item.decidedAt)}.</strong> {item.decisionNote} Nothing moved.
        </>
      );
      break;
    case "withdrawn":
      text = "Withdrawn by the person who proposed it. Nothing moved.";
      break;
    case "proposed":
      if (item.canDecide) text = "Another member of staff proposed this. Check the payment and the reason before you approve.";
      break;
  }
  if (!text) return null;
  return (
    <p role="status" className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
      {text}
    </p>
  );
}

function DecideForm({ item, busy, error, onCommand }: { item: RefundDetail; busy: boolean; error: string | null; onCommand: (command: Command) => void }) {
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
    <form aria-label="Decide the refund" className="flex flex-col gap-3 border-t border-divider pt-4" onSubmit={(event) => event.preventDefault()}>
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
          Approving takes {formatExact(item.amount)} out of the investment it was waiting for and asks Pesapal, once, to pay{" "}
          {formatExact(item.total)} back to the {cardWords(item)}. You will be asked to confirm it is you.
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
            <Button disabled={busy || item.refusal !== null} onClick={() => onCommand({ action: "approve" })} className="h-12 flex-[1_1_180px]">
              {busy ? "Saving" : "Approve and pay it back"}
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

function FindingForm({ item, busy, error, onCommand }: { item: RefundDetail; busy: boolean; error: string | null; onCommand: (command: Command) => void }) {
  const ids = useId();
  // A refund already seen as requested can only be found paid or not paid now.
  const choices: Found[] = item.status === "requested" ? ["paid", "not_paid"] : ["paid", "requested", "not_paid"];
  const [found, setFound] = useState<Found | null>(null);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!found) {
      setProblem("Choose what Pesapal shows.");
      return;
    }
    if (!note.trim()) {
      setProblem("Say where you saw it, such as Pesapal's dashboard or the statement for a date.");
      return;
    }
    setProblem(null);
    onCommand({ action: "finding", found, note: note.trim() });
  }

  return (
    <form aria-label="Say what Pesapal shows" className="flex flex-col gap-3 border-t border-divider pt-4" onSubmit={submit}>
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 text-md font-bold">What Pesapal shows</legend>
        {choices.map((choice) => (
          <label
            key={choice}
            className={cn("flex cursor-pointer gap-3 rounded-lg border px-4 py-3", found === choice ? "border-2 border-black px-[15px] py-[11px]" : "border-border")}
          >
            <input
              type="radio"
              name={`${ids}-found`}
              value={choice}
              checked={found === choice}
              onChange={() => {
                setFound(choice);
                setProblem(null);
              }}
              className="mt-1"
            />
            <span className="flex flex-col gap-0.5">
              <span className="text-md font-bold">{FOUND_LABELS[choice].label}</span>
              <span className="text-xs text-muted">{FOUND_LABELS[choice].hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-note`}>Where you saw it</Label>
        <textarea
          id={`${ids}-note`}
          value={note}
          maxLength={500}
          rows={2}
          onChange={(event) => {
            setNote(event.target.value);
            setProblem(null);
          }}
          className="rounded-md border border-border-strong bg-surface px-3 py-2 text-md font-medium"
        />
      </div>
      {problem ? <ErrorNote message={problem} /> : error ? <ErrorNote message={error} /> : null}
      <Button type="submit" disabled={busy} className="h-12 self-start px-6">
        {busy ? "Saving" : "Record it"}
      </Button>
      <p className="m-0 text-xs text-muted">The worker carries out what you found. You will be asked to confirm it is you.</p>
    </form>
  );
}

function ProposeForm({ busy, onPropose, onCancel }: { busy: boolean; onPropose: (proposal: Proposal) => Promise<RefundError | null>; onCancel: () => void }) {
  const ids = useId();
  const [values, setValues] = useState<Proposal>({ depositId: "", reason: "", finvestErred: false });
  const [problems, setProblems] = useState<Partial<Record<"depositId" | "reason" | "form", string | undefined>>>({});

  async function submit(event: FormEvent) {
    event.preventDefault();
    const missing: Partial<Record<"depositId" | "reason", string>> = {};
    if (!values.depositId.trim()) missing.depositId = "Enter the ID of the card payment to pay back.";
    if (!values.reason.trim()) missing.reason = "Say why it goes back, for the person who checks it.";
    setProblems(missing);
    if (Object.keys(missing).length > 0) return;
    const error = await onPropose({ ...values, depositId: values.depositId.trim(), reason: values.reason.trim() });
    if (!error) return;
    const message = refundErrorMessage(error);
    setProblems(error.field && error.field !== "note" ? { [error.field]: message } : { form: message });
  }

  return (
    <form
      aria-label="Propose a card refund"
      onSubmit={(event) => void submit(event)}
      className="flex max-w-[640px] flex-col gap-4 rounded-xl border border-border bg-surface p-5"
    >
      <div className="flex flex-col gap-1">
        <h3 className="m-0 text-lg font-extrabold">Propose a card refund</h3>
        <p className="m-0 text-md leading-normal text-muted">
          For a confirmed card payment whose whole amount still waits for the investment. If the investment has started, undo it first on
          the investments screen.
        </p>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-deposit`}>Card payment</Label>
        <Input
          id={`${ids}-deposit`}
          value={values.depositId}
          aria-invalid={problems.depositId ? true : undefined}
          aria-describedby={`${ids}-deposit-hint`}
          onChange={(event) => {
            setValues({ ...values, depositId: event.target.value });
            setProblems({ ...problems, depositId: undefined, form: undefined });
          }}
          className="tabular-nums"
        />
        <p id={`${ids}-deposit-hint`} className={cn("m-0 text-xs", problems.depositId ? "font-semibold text-danger" : "text-muted")}>
          {problems.depositId ?? "The payment's ID, from the client's page or Pesapal's export."}
        </p>
      </div>
      <label className="flex cursor-pointer gap-3 rounded-lg border border-border px-4 py-3">
        <input
          type="checkbox"
          checked={values.finvestErred}
          onChange={(event) => setValues({ ...values, finvestErred: event.target.checked })}
          className="mt-1"
        />
        <span className="flex flex-col gap-0.5">
          <span className="text-md font-bold">This is Finvest's own mistake</span>
          <span className="text-xs text-muted">The card charge the client paid goes back too. Otherwise only the investment's amount goes back.</span>
        </span>
      </label>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-reason`}>Why it goes back</Label>
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
          {busy ? "Saving" : "Propose it"}
        </Button>
        <Button type="button" variant="secondary" disabled={busy} onClick={onCancel} className="h-12 flex-[1_1_180px]">
          Cancel
        </Button>
      </div>
      <p className="m-0 text-xs text-muted">Nothing moves yet. A second person checks and approves.</p>
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
      <h3 className="m-0 text-lg font-extrabold">Card refunds could not be loaded</h3>
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
