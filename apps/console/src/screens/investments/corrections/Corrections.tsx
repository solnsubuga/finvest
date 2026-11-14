import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import type { Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { formatExact, formatStamp } from "../../products/format.ts";
import {
  CorrectionsError,
  correctionProblemText,
  correctionsErrorMessage,
  failureText,
  type Correction,
  type CorrectionField,
  type CorrectionKind,
  type CorrectionProblem,
  type CorrectionsApi,
  type NewCorrection,
} from "./api.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const REASON_LIMIT = 500;

const KINDS: readonly { kind: CorrectionKind; label: string; hint: string }[] = [
  {
    kind: "unwind",
    label: "Undo a running investment",
    hint: "For an investment that should never have started, such as a deposit matched to the wrong client. The principal goes back as money waiting to start, and the interest so far is reversed.",
  },
  {
    kind: "refund",
    label: "Refund money waiting to start",
    hint: "For money the client paid in themselves for an investment that was cancelled or undone. It is paid to one of their approved payout accounts.",
  },
];

const KIND_TITLES: Record<CorrectionKind, string> = { unwind: "Undo an investment", refund: "Refund to the client" };

const STATUS: Record<Correction["status"], { label: string; className: string }> = {
  proposed: { label: "Waiting for a second person", className: "bg-warning-surface text-warning-text" },
  approved: { label: "Approved, moving the money", className: "bg-warning-surface text-warning-text" },
  done: { label: "Done", className: "bg-success-surface text-success" },
  failed: { label: "Nothing moved", className: "bg-danger-surface text-danger" },
  sent_back: { label: "Sent back", className: "bg-surface-sunken text-muted" },
  withdrawn: { label: "Withdrawn", className: "bg-surface-sunken text-muted" },
};

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; items: readonly Correction[] };
type Failure = { on: string; message: string; problems: readonly CorrectionProblem[] };

interface CorrectionsSectionProps {
  api: CorrectionsApi;
  directory: Directory;
  /** The signed-in member of staff, to tell their own proposals apart. */
  me: string;
  /** `investment_corrections.change`: propose and decide. The API checks it again. */
  canChange: boolean;
  newKey?: () => string;
}

/** Undoing an investment started by mistake, and refunding money that never started one. Two people each. */
export function CorrectionsSection({ api, directory, me, canChange, newKey = () => crypto.randomUUID() }: CorrectionsSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [writing, setWriting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<Failure | null>(null);
  const [names, setNames] = useState<Record<string, string | null>>({});
  const [clients, setClients] = useState<Record<string, string | null>>({});
  const asked = useRef(new Set<string>());
  const askedClients = useRef(new Set<string>());
  // One key per attempt: the same request sent again after a lost answer is not done twice.
  const pending = useRef(new Map<string, { request: string; key: string }>());

  const fetchList = useCallback(
    (): Promise<Load> =>
      api.list().then(
        (items) => {
          for (const staffId of items.flatMap((item) => [item.proposedBy, ...(item.decidedBy ? [item.decidedBy] : [])])) {
            if (staffId === me || asked.current.has(staffId)) continue;
            asked.current.add(staffId);
            void directory.staffName(staffId).then(
              (name) => setNames((known) => ({ ...known, [staffId]: name })),
              () => setNames((known) => ({ ...known, [staffId]: null })),
            );
          }
          for (const clientId of items.map((item) => item.clientId)) {
            if (askedClients.current.has(clientId)) continue;
            askedClients.current.add(clientId);
            void directory.client(clientId).then(
              (card) => setClients((known) => ({ ...known, [clientId]: card?.name ?? null })),
              () => setClients((known) => ({ ...known, [clientId]: null })),
            );
          }
          return { state: "ready", items };
        },
        (error: unknown) => ({ state: "failed", message: correctionsErrorMessage(error) }),
      ),
    [api, directory, me],
  );

  useEffect(() => {
    let current = true;
    void fetchList().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchList]);

  function keyFor(on: string, request: string): string {
    const waiting = pending.current.get(on);
    if (waiting?.request === request) return waiting.key;
    const key = newKey();
    pending.current.set(on, { request, key });
    return key;
  }

  async function run(on: string, request: unknown, command: (key: string) => Promise<Correction>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      await command(keyFor(on, JSON.stringify(request)));
      pending.current.delete(on);
      return true;
    } catch (error) {
      // Only an unanswered request keeps its key, so trying again repeats the same one.
      if (!(error instanceof CorrectionsError && error.code === "unavailable")) pending.current.delete(on);
      setFailed({ on, message: correctionsErrorMessage(error), problems: error instanceof CorrectionsError ? error.problems : [] });
      return false;
    } finally {
      // The list always shows what Finvest now holds, after a change or a refusal.
      const fresh = await fetchList();
      if (fresh.state === "ready") setLoad(fresh);
      setBusy(null);
    }
  }

  const who = (staffId: string) => (staffId === me ? "you" : (names[staffId] ?? "a member of staff"));
  const waiting = load.state === "ready" ? load.items.filter((item) => item.status === "proposed").length : 0;

  return (
    <section aria-labelledby="corrections" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex max-w-[720px] flex-col gap-1">
          <h2 id="corrections" className="m-0 text-xl font-extrabold">
            Undoing and refunds
          </h2>
          <p className="m-0 text-md leading-normal text-muted">
            One person proposes, someone else approves, and Finvest then moves the money.{" "}
            {load.state !== "ready"
              ? null
              : waiting === 0
                ? "None is waiting."
                : waiting === 1
                  ? "1 is waiting for a second person."
                  : `${waiting} are waiting for a second person.`}
          </p>
        </div>
        {canChange && !writing && load.state === "ready" ? (
          <Button
            className="px-[18px]"
            onClick={() => {
              setWriting(true);
              setFailed(null);
            }}
          >
            Propose a correction
          </Button>
        ) : null}
      </div>

      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading corrections
        </p>
      ) : load.state === "failed" ? (
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">Corrections could not be loaded</h3>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchList().then(setLoad);
            }}
          >
            Try again
          </Button>
        </section>
      ) : (
        <div className="flex max-w-[760px] flex-col gap-3">
          {writing ? (
            <ProposeForm
              busy={busy === "new"}
              error={failed?.on === "new" ? failed.message : null}
              refused={failed?.on === "new" ? failed.problems : []}
              onCancel={() => {
                setWriting(false);
                setFailed(null);
                pending.current.delete("new");
              }}
              onSubmit={(input) =>
                run("new", input, (key) => api.propose(key, input)).then((done) => {
                  if (done) setWriting(false);
                  return done;
                })
              }
            />
          ) : null}
          {load.items.length === 0 && !writing ? (
            <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
              <h3 className="m-0 text-[17px] font-extrabold">Nothing has been corrected</h3>
              <p className="m-0 text-md leading-normal text-muted">
                Use this when money went the wrong way: an investment that should never have started, or money a client paid in that
                should go back to them.
              </p>
            </div>
          ) : (
            load.items.map((item) => (
              <CorrectionCard
                key={`${item.id}:${item.status}`}
                item={item}
                mine={item.proposedBy === me}
                canChange={canChange}
                who={who}
                clientName={clients[item.clientId] ?? null}
                busy={busy === item.id}
                locked={busy !== null}
                error={failed?.on === item.id ? failed.message : null}
                onApprove={() => run(item.id, "approve", (key) => api.approve(key, item.id))}
                onSendBack={(note) => run(item.id, { note }, (key) => api.sendBack(key, item.id, note))}
                onWithdraw={() => run(item.id, "withdraw", (key) => api.withdraw(key, item.id))}
              />
            ))
          )}
        </div>
      )}
    </section>
  );
}

interface ProposeFormProps {
  busy: boolean;
  error: string | null;
  refused: readonly CorrectionProblem[];
  onCancel: () => void;
  onSubmit: (input: NewCorrection) => Promise<boolean>;
}

function ProposeForm({ busy, error, refused, onCancel, onSubmit }: ProposeFormProps) {
  const [kind, setKind] = useState<CorrectionKind>("unwind");
  const [investmentId, setInvestmentId] = useState("");
  const [destinationId, setDestinationId] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);

  const missing: CorrectionProblem[] = [
    ...(investmentId.trim() ? [] : [{ field: "investment_id" as const, code: "required" }]),
    ...(reason.trim() ? [] : [{ field: "reason" as const, code: "required" }]),
    ...(reason.length > REASON_LIMIT ? [{ field: "reason" as const, code: "too_long" }] : []),
    ...(kind === "refund" && !destinationId.trim() ? [{ field: "destination_id" as const, code: "required" }] : []),
  ];
  const shown = [...(tried ? missing : missing.filter((p) => p.code !== "required")), ...refused];
  const codesFor = (field: CorrectionField) => [...new Set(shown.filter((p) => p.field === field).map((p) => p.code))];

  function field(name: CorrectionField, label: string, hint: string, value: string, set: (next: string) => void, rows?: number) {
    const codes = codesFor(name);
    const props = {
      value,
      "aria-invalid": codes.length > 0,
      onChange: (event: { target: { value: string } }) => set(event.target.value),
      className: cn(INPUT, rows ? "" : "font-mono"),
    };
    return (
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        <span className="flex justify-between gap-2">
          {label}
          {rows ? (
            <span className={cn("text-xs font-semibold", value.length > REASON_LIMIT ? "text-danger" : "text-muted")}>
              {value.length} of {REASON_LIMIT}
            </span>
          ) : null}
        </span>
        <span className="text-xs font-medium text-muted">{hint}</span>
        {rows ? <textarea rows={rows} {...props} /> : <input spellCheck={false} autoComplete="off" {...props} />}
        {codes.map((code) => (
          <span key={code} className="text-xs font-bold text-danger">
            {correctionProblemText(code)}
          </span>
        ))}
      </label>
    );
  }

  return (
    <form
      aria-label="Propose a correction"
      className="flex flex-col gap-3 rounded-[20px] border border-border bg-surface p-5"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (missing.length > 0) return;
        void onSubmit({
          kind,
          investmentId: investmentId.trim(),
          reason: reason.trim(),
          destinationId: kind === "refund" ? destinationId.trim() : null,
        });
      }}
    >
      <h3 className="m-0 text-[17px] font-extrabold">Propose a correction</h3>
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 p-0 text-sm font-bold">What needs putting right?</legend>
        {KINDS.map((option) => (
          <label
            key={option.kind}
            className={cn(
              "flex cursor-pointer gap-3 rounded-lg border p-3.5",
              kind === option.kind ? "border-2 border-ink p-[13px]" : "border-border-strong",
            )}
          >
            <input type="radio" name="kind" checked={kind === option.kind} onChange={() => setKind(option.kind)} className="mt-1 size-4 accent-black" />
            <span className="flex flex-col gap-0.5">
              <span className="text-md font-bold">{option.label}</span>
              <span className="text-sm leading-normal text-muted">{option.hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {kind === "refund" ? (
        <p className="m-0 rounded-md bg-warning-surface px-3 py-2.5 text-sm leading-normal font-semibold text-warning-text">
          Only refund money this client paid in themselves. A deposit credited to the wrong client is put right by reversing it in
          payments, never with a refund here.
        </p>
      ) : null}
      {field("investment_id", "Investment ID", "The ID of the investment to correct, as payments or the client's page gives it.", investmentId, setInvestmentId)}
      {kind === "refund"
        ? field("destination_id", "Payout account ID", "One of the client's approved payout accounts, from their client page.", destinationId, setDestinationId)
        : null}
      {field("reason", "Why?", "Staff only, for the person who approves it and the audit trail. Name the payment or statement line.", reason, setReason, 3)}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <p className="m-0 text-xs leading-normal text-muted">
        Nothing moves until someone else approves it. The amount is fixed now, and Finvest checks it again before moving the money.
      </p>
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving" : "Send for approval"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

interface CorrectionCardProps {
  item: Correction;
  mine: boolean;
  canChange: boolean;
  who: (staffId: string) => string;
  /** From the shared directory, once known. */
  clientName: string | null;
  busy: boolean;
  /** Another command is running on the section. */
  locked: boolean;
  error: string | null;
  onApprove: () => Promise<boolean>;
  onSendBack: (note: string) => Promise<boolean>;
  onWithdraw: () => Promise<boolean>;
}

function CorrectionCard({ item, mine, canChange, who, clientName, busy, locked, error, onApprove, onSendBack, onWithdraw }: CorrectionCardProps) {
  const [sendingBack, setSendingBack] = useState(false);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const status = STATUS[item.status];
  const waiting = item.status === "proposed";
  const decided = item.status === "sent_back" ? "Sent back by " : item.status === "withdrawn" ? "Withdrawn by " : "Approved by ";

  return (
    <article aria-label={`${KIND_TITLES[item.kind]}, ${formatExact(item.amount)}`} className="flex flex-col gap-2.5 rounded-[20px] border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="m-0 text-[17px] font-extrabold">
          {KIND_TITLES[item.kind]}, <span className="tabular-nums">{formatExact(item.amount)}</span>
        </h3>
        <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold", status.className)}>{status.label}</span>
      </div>
      <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted">Investment</dt>
        <dd className="m-0 font-mono break-all">{item.investmentId}</dd>
        <dt className="text-muted">Client</dt>
        <dd className="m-0 break-all">
          {clientName ? <span className="font-bold">{clientName}</span> : null}
          <span className={cn("font-mono", clientName && "block text-xs text-muted")}>{item.clientId}</span>
        </dd>
        {item.destinationId ? (
          <>
            <dt className="text-muted">Paid to</dt>
            <dd className="m-0 font-mono break-all">{item.destinationId}</dd>
          </>
        ) : null}
      </dl>
      <dl className="m-0 flex flex-col gap-1 text-sm text-muted">
        <div>
          <dt className="inline">Proposed by </dt>
          <dd className="m-0 inline">
            {who(item.proposedBy)}, {formatStamp(item.proposedAt)}. <span className="text-ink">{item.reason}</span>
          </dd>
        </div>
        {item.decidedBy && item.decidedAt ? (
          <div>
            <dt className="inline">{decided}</dt>
            <dd className="m-0 inline">
              {who(item.decidedBy)}, {formatStamp(item.decidedAt)}
              {item.decisionNote ? (
                <>
                  . <span className="text-ink">{item.decisionNote}</span>
                </>
              ) : null}
            </dd>
          </div>
        ) : null}
      </dl>
      {item.status === "done" ? (
        <p className="m-0 text-sm text-ink">
          {item.kind === "unwind"
            ? "The principal is back as money waiting to start, and the interest so far is reversed. The client no longer sees the investment."
            : "The payout is with payouts, which tells the client when it is on its way."}
        </p>
      ) : null}
      {item.status === "failed" && item.failureCode ? (
        <p className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {failureText(item.failureCode)} Nothing moved. Propose it again once it is put right.
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      {waiting && !canChange ? <p className="m-0 text-sm text-muted">Someone who can correct investments approves it.</p> : null}
      {waiting && canChange && mine ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted">Someone else approves it.</span>
          <Button variant="secondary" disabled={locked} onClick={() => void onWithdraw()}>
            {busy ? "Saving" : "Withdraw it"}
          </Button>
        </div>
      ) : null}
      {waiting && canChange && !mine && !sendingBack ? (
        <div className="flex flex-col gap-2">
          <p className="m-0 text-sm text-muted">
            {item.kind === "unwind"
              ? "Approving starts the undo: Finvest checks the figures again, reverses the interest so far, and the client stops seeing the investment."
              : "Approving starts the payout to this account once Finvest has checked the amount again. A payout cannot be called back."}
          </p>
          <div className="flex gap-2">
            <Button disabled={locked} onClick={() => void onApprove()}>
              {busy ? "Saving" : "Approve"}
            </Button>
            <Button variant="secondary" disabled={locked} onClick={() => setSendingBack(true)}>
              Send back
            </Button>
          </div>
        </div>
      ) : null}
      {waiting && sendingBack ? (
        <form
          aria-label="Send the correction back"
          className="flex flex-col gap-2 border-t border-divider pt-3"
          onSubmit={(event) => {
            event.preventDefault();
            setTried(true);
            if (note.trim().length === 0) return;
            void onSendBack(note.trim());
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            What needs checking?
            <textarea
              value={note}
              rows={2}
              aria-invalid={tried && note.trim().length === 0}
              onChange={(event) => setNote(event.target.value)}
              className={INPUT}
            />
          </label>
          {tried && note.trim().length === 0 ? <p className="m-0 text-xs font-bold text-danger">Say why, so the proposer knows what to check.</p> : null}
          <div className="flex gap-2">
            <Button type="submit" disabled={locked}>
              {busy ? "Saving" : "Send it back"}
            </Button>
            <Button variant="secondary" disabled={locked} onClick={() => setSendingBack(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </article>
  );
}
