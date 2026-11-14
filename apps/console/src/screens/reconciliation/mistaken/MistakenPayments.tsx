import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import { formatExact, formatMoment } from "../format.ts";
import {
  CorrectionError,
  REFUSALS,
  STEP_LABELS,
  correctionErrorMessage,
  kindOf,
  type CorrectionDetail,
  type CorrectionStatus,
  type CorrectionSummary,
  type Kind,
  type Method,
  type MistakenPaymentsApi,
  type Proposal,
  type Step,
} from "./api.ts";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: T };

type Command = { action: "approve" } | { action: "send_back"; note: string } | { action: "withdraw" };

interface Selected {
  kind: Kind;
  id: string;
}

interface MistakenPaymentsProps {
  api: MistakenPaymentsApi;
  /** Whether the signed-in person may propose, approve, send back and withdraw. Seeing needs less. */
  canChange: boolean;
  /** Makes the idempotency key for one command. Every try at the same command carries the same key. */
  newKey?: () => string;
}

export const STATUS_LABELS: Record<CorrectionStatus, string> = {
  proposed: "Needs a second person",
  approved: "Approved, being carried out",
  done: "Taken back",
  failed: "Failed, nothing moved",
  applied: "Recorded",
  sent_back: "Sent back",
  withdrawn: "Withdrawn",
};

const METHOD_LABELS: Record<Method, string> = {
  mtn: "MTN Mobile Money",
  airtel: "Airtel Money",
  bank_transfer: "Bank transfer",
};

/** Why the worker could not take a payment back, worded for staff. */
const FAILURES: Record<string, string> = {
  money_moved_on: "The money stopped waiting for the investment before the worker reached it, most likely because the investment started.",
  already_reversed: "The payment had already been taken back.",
  not_confirmed: "The payment was no longer confirmed.",
};

const PARAM: Record<Kind, string> = { reversal: "reversal", receivable: "receivable" };

const sameItem = (a: Selected | null, b: Selected | null) => a !== null && b !== null && a.kind === b.kind && a.id === b.id;
const itemKey = (at: Selected) => `${at.kind}:${at.id}`;
const selectionOf = (item: CorrectionSummary): Selected => ({ kind: kindOf(item.step), id: item.id });

/**
 * Payments credited by mistake: while the money still waits for the investment, two people take
 * the payment back. Once the money has left, two people record what the client owes, and later
 * two people record it recovered or write it off.
 */
export function MistakenPayments({ api, canChange, newKey = () => crypto.randomUUID() }: MistakenPaymentsProps) {
  const [params, setParams] = useSearchParams();
  const requested: Selected | null = params.get("reversal")
    ? { kind: "reversal", id: params.get("reversal") as string }
    : params.get("receivable")
      ? { kind: "receivable", id: params.get("receivable") as string }
      : null;
  const [list, setList] = useState<Load<{ items: CorrectionSummary[]; owed: CorrectionSummary[] }>>({ state: "loading" });
  // Each step's detail is kept under its own key. The address can name the step it named before
  // for one render after a proposal, and a fetch started then must not evict the new step.
  const [loaded, setLoadedAll] = useState<Record<string, Load<CorrectionDetail>>>({});
  const setLoaded = ({ at, load }: { at: Selected; load: Load<CorrectionDetail> }) =>
    setLoadedAll((known) => ({ ...known, [itemKey(at)]: load }));
  const [proposing, setProposing] = useState<Partial<Proposal> | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ at: Selected; message: string } | null>(null);
  // The step being worked on stays open after a decision, though it is no longer waiting.
  const [kept, setKept] = useState<Selected | null>(null);
  // The key of a command that has not been answered yet, so a second press replays the first.
  const pending = useRef<{ request: string; key: string } | null>(null);

  const fetchList = useCallback(
    (): Promise<Load<{ items: CorrectionSummary[]; owed: CorrectionSummary[] }>> =>
      Promise.all([api.list(), api.outstanding()]).then(
        ([items, owed]) => ({ state: "ready", value: { items, owed } }),
        (error: unknown) => ({ state: "failed", message: correctionErrorMessage(error) }),
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

  const items = list.state === "ready" ? list.value.items : null;
  const first = items?.find((item) => item.status === "proposed") ?? items?.[0];
  const selected: Selected | null = proposing ? null : (requested ?? kept ?? (first ? selectionOf(first) : null));
  const selectedKind = selected?.kind;
  const selectedId = selected?.id;

  const fetchDetail = useCallback(
    (at: Selected) =>
      api.get(at.kind, at.id).then(
        (value): Load<CorrectionDetail> => ({ state: "ready", value }),
        (error: unknown): Load<CorrectionDetail> => ({ state: "failed", message: correctionErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    if (!selectedKind || !selectedId) return;
    const at = { kind: selectedKind, id: selectedId };
    let current = true;
    void fetchDetail(at).then((load) => current && setLoaded({ at, load }));
    return () => {
      current = false;
    };
  }, [fetchDetail, selectedKind, selectedId]);

  const detail: Load<CorrectionDetail> = (selected && loaded[itemKey(selected)]) || { state: "loading" };

  function keyFor(request: string): string {
    if (pending.current?.request === request) return pending.current.key;
    const key = newKey();
    pending.current = { request, key };
    return key;
  }

  function settled(error: unknown) {
    // Only a definite refusal closes the command. A lost answer reuses the key on the next try.
    if (!(error instanceof CorrectionError) || error.code !== "unavailable") pending.current = null;
  }

  async function run(at: Selected, command: Command) {
    setBusy(true);
    setFailure(null);
    setKept(at);
    const key = keyFor(JSON.stringify({ at, command }));
    try {
      const updated =
        command.action === "approve"
          ? await api.approve(key, at.kind, at.id)
          : command.action === "send_back"
            ? await api.sendBack(key, at.kind, at.id, command.note)
            : await api.withdraw(key, at.kind, at.id);
      pending.current = null;
      setLoaded({ at, load: { state: "ready", value: updated } });
      await reloadList();
    } catch (error) {
      settled(error);
      setFailure({ at, message: correctionErrorMessage(error) });
      if (error instanceof CorrectionError && error.code !== "unavailable" && error.code !== "confirm_it_is_you") {
        setLoaded({ at, load: await fetchDetail(at) });
        await reloadList();
      }
    } finally {
      setBusy(false);
    }
  }

  async function propose(proposal: Proposal): Promise<CorrectionError | null> {
    setBusy(true);
    const key = keyFor(JSON.stringify({ propose: proposal }));
    try {
      const made = await api.propose(key, proposal);
      const at = selectionOf(made);
      pending.current = null;
      setProposing(null);
      setKept(at);
      setLoaded({ at, load: { state: "ready", value: made } });
      setParams({ [PARAM[at.kind]]: made.id });
      await reloadList();
      return null;
    } catch (error) {
      settled(error);
      return error instanceof CorrectionError ? error : new CorrectionError("unavailable");
    } finally {
      setBusy(false);
    }
  }

  function startProposal(values: Partial<Proposal> = {}) {
    pending.current = null;
    setProposing(values);
  }

  const waiting = items?.filter((item) => item.status === "proposed").length ?? 0;
  const summary =
    items === null
      ? "A payment credited by mistake is taken back, or recorded as owed once its money has left, by two people."
      : waiting === 0
        ? "Nothing is waiting for a second person."
        : `${waiting} waiting for a second person.`;

  return (
    <section aria-labelledby="mistaken-title" className="flex flex-col gap-4 border-t border-divider pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="mistaken-title" className="m-0 text-xl font-extrabold">
            Payments credited by mistake
          </h2>
          <p className="m-0 text-md text-muted">{summary}</p>
        </div>
        {canChange && !proposing ? (
          <Button variant="secondary" onClick={() => startProposal()}>
            Propose a step
          </Button>
        ) : null}
      </div>

      {list.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading mistaken payments
        </p>
      ) : list.state === "failed" ? (
        <Failure message={list.message} onRetry={() => void reloadList()} />
      ) : (
        <>
          {list.value.owed.length > 0 ? (
            <Owed items={list.value.owed} canChange={canChange} onClear={(depositId, step) => startProposal({ depositId, step })} />
          ) : null}
          <div className="flex flex-wrap items-start gap-5">
            {list.value.items.length > 0 ? (
              <div className="min-w-0 flex-[1_1_240px]">
                <StepList
                  items={list.value.items}
                  selected={selected}
                  onSelect={(at) => {
                    setKept(null);
                    setProposing(null);
                    pending.current = null;
                    setFailure(null);
                    setParams({ [PARAM[at.kind]]: at.id });
                  }}
                />
              </div>
            ) : null}
            <div className="min-w-0 flex-[3_1_520px]">
              {proposing ? (
                <ProposeForm initial={proposing} busy={busy} onPropose={propose} onCancel={() => setProposing(null)} />
              ) : list.value.items.length === 0 ? (
                <div className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
                  <h3 className="m-0 text-lg font-extrabold">No mistaken payments</h3>
                  <p className="m-0 text-md leading-normal text-muted">
                    If a provider or bank confirmed a payment whose money never reached Finvest, propose taking it back here. Once its
                    money has left, record what the client owes instead. A second person checks and approves every step.
                  </p>
                </div>
              ) : detail.state === "loading" ? (
                <p role="status" className="m-0 rounded-xl border border-border bg-surface p-5 text-md text-muted">
                  Loading this step
                </p>
              ) : detail.state === "failed" ? (
                <Failure message={detail.message} />
              ) : (
                <Correction
                  key={`${detail.value.id}:${detail.value.status}`}
                  item={detail.value}
                  canChange={canChange}
                  busy={busy}
                  error={failure && selected && sameItem(failure.at, selected) ? failure.message : null}
                  onCommand={(command) => selected && void run(selected, command)}
                />
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function Owed({
  items,
  canChange,
  onClear,
}: {
  items: readonly CorrectionSummary[];
  canChange: boolean;
  onClear: (depositId: string, step: Step) => void;
}) {
  return (
    <section aria-labelledby="owed-title" className="flex flex-col gap-2 rounded-xl border border-warning bg-warning-surface px-4 py-3">
      <h3 id="owed-title" className="m-0 text-md font-extrabold text-warning-text">
        {items.length === 1 ? "1 payment is still owed" : `${items.length} payments are still owed`}
      </h3>
      <p className="m-0 text-md leading-normal text-warning-text">The daily close reports these until each is recovered or written off.</p>
      <ul aria-label="Still owed" className="m-0 flex list-none flex-col gap-2 p-0">
        {items.map((item) => (
          <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-surface px-3 py-2">
            <span className="flex flex-col">
              <span className="text-md font-extrabold tabular-nums">{formatExact(item.amount)}</span>
              <span className="text-xs text-muted tabular-nums">Payment {item.depositId}</span>
            </span>
            {canChange ? (
              <span className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => onClear(item.depositId, "recover")}>
                  The money arrived
                </Button>
                <Button variant="secondary" onClick={() => onClear(item.depositId, "write_off")}>
                  Write it off
                </Button>
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function StepList({
  items,
  selected,
  onSelect,
}: {
  items: readonly CorrectionSummary[];
  selected: Selected | null;
  onSelect: (at: Selected) => void;
}) {
  return (
    <ul aria-label="Mistaken payment steps" className="m-0 flex list-none flex-col gap-2 p-0">
      {items.map((item) => {
        const at = selectionOf(item);
        const current = sameItem(at, selected);
        return (
          <li key={`${at.kind}:${item.id}`}>
            <button
              type="button"
              aria-current={current ? "true" : undefined}
              onClick={() => onSelect(at)}
              className={cn(
                "flex w-full flex-col gap-1 rounded-lg border bg-surface px-4 py-3 text-left",
                current ? "border-2 border-black px-[15px] py-[11px]" : "border-border",
              )}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="text-md font-extrabold tabular-nums">{formatExact(item.amount)}</span>
                <StatusPill status={item.status} />
              </span>
              <span className="text-sm font-semibold">{STEP_LABELS[item.step]}</span>
              <span className="text-xs text-muted">Proposed {formatMoment(item.proposedAt)}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function StatusPill({ status }: { status: CorrectionStatus }) {
  const tone =
    status === "done" || status === "applied"
      ? "bg-success-surface text-success"
      : status === "proposed" || status === "approved"
        ? "bg-warning-surface text-warning-text"
        : status === "failed"
          ? "bg-danger-surface text-danger"
          : "bg-surface-sunken text-muted";
  return <span className={cn("shrink-0 rounded-pill px-2.5 py-1 text-xs font-bold", tone)}>{STATUS_LABELS[status]}</span>;
}

function Correction({
  item,
  canChange,
  busy,
  error,
  onCommand,
}: {
  item: CorrectionDetail;
  canChange: boolean;
  busy: boolean;
  error: string | null;
  onCommand: (command: Command) => void;
}) {
  const open = item.status === "proposed";
  const reversal = item.step === "reverse";
  return (
    <article
      aria-label={`${STEP_LABELS[item.step]}, ${formatExact(item.amount)}`}
      className="flex flex-col gap-5 rounded-xl border border-border bg-surface p-5"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-xs font-bold tracking-[0.6px] text-muted uppercase">{STEP_LABELS[item.step]}</span>
          <h3 className="m-0 text-2xl font-extrabold tabular-nums">{formatExact(item.amount)}</h3>
          <p className="m-0 text-md text-muted">Proposed {formatMoment(item.proposedAt)}</p>
        </div>
        <StatusPill status={item.status} />
      </header>

      <Section title="The payment">
        <dl className="m-0">
          <Row name="Payment" value={<span className="tabular-nums">{item.depositId}</span>} />
          <Row name="Paid by" value={METHOD_LABELS[item.method]} />
          <Row name="Client" value={<span className="tabular-nums">{item.clientId}</span>} />
          {item.investmentId ? <Row name="Investment" value={<span className="tabular-nums">{item.investmentId}</span>} /> : null}
          <Row name={reversal ? "Waiting for the investment now" : "The client owes now"} value={formatExact(item.balance)} />
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
      {open && !canChange ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
          You can see this step. Approving, sending back and withdrawing need the permission to put mistaken payments right.
        </p>
      ) : null}
      {open && canChange && item.canDecide ? <DecideForm item={item} busy={busy} error={error} onCommand={onCommand} /> : null}
      {open && canChange && item.yours ? (
        <div className="flex flex-col gap-3 border-t border-divider pt-4">
          <p className="m-0 text-md leading-normal">
            You proposed this, so a second person with the permission to put mistaken payments right has to approve it. You can withdraw
            it until they do.
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

const DONE_WORDS: Record<Step, string> = {
  reverse: "The payment no longer counts for the investment, and the client was told it was taken back. They can pay for it again.",
  open: "The client owes this amount. The daily close reports it until it is recovered or written off.",
  recover: "The money arrived, and what the client owed is cleared.",
  write_off: "What the client owed is written off as Finvest's loss.",
};

function Outcome({ item }: { item: CorrectionDetail }) {
  const when = item.decidedAt ? ` ${formatMoment(item.decidedAt)}` : "";
  let text: ReactNode = null;
  if (item.status === "done" || item.status === "applied") {
    text = (
      <>
        <strong>{item.status === "done" ? "Taken back" : "Recorded"}{when}.</strong> {DONE_WORDS[item.step]}
      </>
    );
  } else if (item.status === "approved") {
    text = (
      <>
        <strong>Approved{when}.</strong> The worker is taking the payment back now. Reload in a moment.
      </>
    );
  } else if (item.status === "failed") {
    text = (
      <>
        <strong>Failed. Nothing moved.</strong> {FAILURES[item.failureCode ?? ""] ?? "The payment or its money changed after approval."} Propose
        again once the money is back, or record what the client owes.
      </>
    );
  } else if (item.status === "sent_back") {
    text = (
      <>
        <strong>Sent back{when}.</strong> {item.decisionNote} Nothing moved.
      </>
    );
  } else if (item.status === "withdrawn") {
    text = "Withdrawn by the person who proposed it. Nothing moved.";
  } else if (item.yours) {
    return null;
  } else if (item.canDecide) {
    text = "Another member of staff proposed this. Check the payment with the provider or the bank statement before you approve.";
  }
  if (!text) return null;
  return (
    <p role="status" className="m-0 rounded-md bg-surface-sunken px-3.5 py-3 text-md leading-normal">
      {text}
    </p>
  );
}

const APPROVE_WORDS: Record<Step, { hint: (amount: string) => string; button: string }> = {
  reverse: {
    hint: (amount) => `Approving takes ${amount} back from the investment it was waiting for, and tells the client.`,
    button: "Approve and take it back",
  },
  open: { hint: (amount) => `Approving records that the client owes ${amount}.`, button: "Approve and record it" },
  recover: { hint: (amount) => `Approving records that ${amount} arrived and clears what the client owed.`, button: "Approve, the money arrived" },
  write_off: { hint: (amount) => `Approving writes off ${amount} as Finvest's loss.`, button: "Approve the write-off" },
};

function DecideForm({
  item,
  busy,
  error,
  onCommand,
}: {
  item: CorrectionDetail;
  busy: boolean;
  error: string | null;
  onCommand: (command: Command) => void;
}) {
  const noteId = useId();
  const [sendingBack, setSendingBack] = useState(false);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const words = APPROVE_WORDS[item.step];

  function sendBack() {
    if (!note.trim()) {
      setProblem("Say why, so the person who proposed it knows what to check.");
      return;
    }
    setProblem(null);
    onCommand({ action: "send_back", note: note.trim() });
  }

  return (
    <form aria-label="Decide the step" className="flex flex-col gap-3 border-t border-divider pt-4" onSubmit={(event) => event.preventDefault()}>
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
          {words.hint(formatExact(item.amount))} You will be asked to confirm it is you.
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
              {busy ? "Saving" : words.button}
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

const STEP_HINTS: Record<Step, string> = {
  reverse: "The money still waits for the investment. If the investment has started, undo it first on the investments screen.",
  open: "The money has left Finvest, for example the investment was paid out or a refund went out.",
  recover: "The money for a payment already recorded as owed has now arrived.",
  write_off: "A payment recorded as owed will not be recovered.",
};

function ProposeForm({
  initial,
  busy,
  onPropose,
  onCancel,
}: {
  initial: Partial<Proposal>;
  busy: boolean;
  onPropose: (proposal: Proposal) => Promise<CorrectionError | null>;
  onCancel: () => void;
}) {
  const ids = useId();
  const [values, setValues] = useState<Proposal>({ step: initial.step ?? "reverse", depositId: initial.depositId ?? "", reason: "" });
  const [problems, setProblems] = useState<Partial<Record<"depositId" | "reason" | "form", string | undefined>>>({});

  async function submit(event: FormEvent) {
    event.preventDefault();
    const missing: Partial<Record<"depositId" | "reason", string>> = {};
    if (!values.depositId.trim()) missing.depositId = "Enter the ID of the payment credited by mistake.";
    if (!values.reason.trim()) missing.reason = "Say what happened, for the person who checks it.";
    setProblems(missing);
    if (Object.keys(missing).length > 0) return;
    const error = await onPropose({ step: values.step, depositId: values.depositId.trim(), reason: values.reason.trim() });
    if (!error) return;
    const message = correctionErrorMessage(error);
    setProblems(error.field && error.field !== "note" ? { [error.field]: message } : { form: message });
  }

  return (
    <form
      aria-label="Propose a step"
      onSubmit={(event) => void submit(event)}
      className="flex max-w-[640px] flex-col gap-4 rounded-xl border border-border bg-surface p-5"
    >
      <div className="flex flex-col gap-1">
        <h3 className="m-0 text-lg font-extrabold">Propose a step</h3>
        <p className="m-0 text-md leading-normal text-muted">
          For a payment confirmed by mistake, whose money never reached Finvest. A bank transfer matched to the wrong client is moved
          instead, under payments credited to the wrong client.
        </p>
      </div>
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 text-md font-bold">What to do</legend>
        {(Object.keys(STEP_LABELS) as Step[]).map((step) => (
          <label
            key={step}
            className={cn(
              "flex cursor-pointer gap-3 rounded-lg border px-4 py-3",
              values.step === step ? "border-2 border-black px-[15px] py-[11px]" : "border-border",
            )}
          >
            <input
              type="radio"
              name={`${ids}-step`}
              value={step}
              checked={values.step === step}
              onChange={() => {
                setValues({ ...values, step });
                setProblems({});
              }}
              className="mt-1"
            />
            <span className="flex flex-col gap-0.5">
              <span className="text-md font-bold">{STEP_LABELS[step]}</span>
              <span className="text-xs text-muted">{STEP_HINTS[step]}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-deposit`}>Payment</Label>
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
          {problems.depositId ?? "The payment's ID, from the client's page or the provider's report."}
        </p>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-reason`}>What happened</Label>
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
      <h3 className="m-0 text-lg font-extrabold">Mistaken payments could not be loaded</h3>
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
