import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import type { Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { formatDay, formatStamp } from "../../products/format.ts";
import {
  RunRequestsError,
  runProblemText,
  runRequestsErrorMessage,
  type NewRunRequest,
  type RunKind,
  type RunProblem,
  type RunRequest,
  type RunRequestsApi,
} from "./api.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const REASON_LIMIT = 500;
// Decided requests stay in view for a while, so the person who asked sees what became of it.
const DECIDED_SHOWN = 3;

const WORDS: Record<RunKind, { title: string; button: string; what: string; approve: string; empty: string }> = {
  interest: {
    title: "Run interest again",
    button: "Run again for a date",
    what: "For a day whose interest run stopped part way or left investments behind. It only posts what is still owed for that day, so nothing is paid twice.",
    approve: "Approving queues the interest run for this day straight away. It posts only what is still owed.",
    empty: "No interest run has been asked for again.",
  },
  maturity: {
    title: "Run maturities again",
    button: "Run today again",
    what: "For terms this morning's run left open. Today's run closes every term due by today, rolling it over or paying it out as the client chose.",
    approve: "Approving runs today's maturities again straight away. It closes only the terms still open.",
    empty: "Today's maturity run has not been asked for again.",
  },
};

const STATUS: Record<RunRequest["status"], { label: string; className: string }> = {
  proposed: { label: "Waiting for a second person", className: "bg-warning-surface text-warning-text" },
  approved: { label: "Approved, run queued", className: "bg-success-surface text-success" },
  declined: { label: "Declined", className: "bg-surface-sunken text-muted" },
  withdrawn: { label: "Withdrawn", className: "bg-surface-sunken text-muted" },
};

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; items: readonly RunRequest[] };
type Failure = { on: string; message: string; problems: readonly RunProblem[] };

interface RunAgainProps {
  api: RunRequestsApi;
  directory: Directory;
  kind: RunKind;
  /** The signed-in member of staff. */
  me: string;
  /** `investment_corrections.change`: ask and decide. The API checks it again. */
  canChange: boolean;
  /** The Kampala business day. */
  today: string;
  newKey?: () => string;
}

/** Asking for a day's interest run or today's maturity run again. Two people each. */
export function RunAgain({ api, directory, kind, me, canChange, today, newKey = () => crypto.randomUUID() }: RunAgainProps) {
  const words = WORDS[kind];
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [writing, setWriting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<Failure | null>(null);
  const [names, setNames] = useState<Record<string, string | null>>({});
  const asked = useRef(new Set<string>());
  const pending = useRef(new Map<string, { request: string; key: string }>());

  const fetchList = useCallback(
    (): Promise<Load> =>
      api.list().then(
        (all) => {
          const items = all.filter((item) => item.kind === kind);
          for (const staffId of items.flatMap((item) => [item.proposedBy, ...(item.decidedBy ? [item.decidedBy] : [])])) {
            if (staffId === me || asked.current.has(staffId)) continue;
            asked.current.add(staffId);
            void directory.staffName(staffId).then(
              (name) => setNames((known) => ({ ...known, [staffId]: name })),
              () => setNames((known) => ({ ...known, [staffId]: null })),
            );
          }
          return { state: "ready", items };
        },
        (error: unknown) => ({ state: "failed", message: runRequestsErrorMessage(error) }),
      ),
    [api, directory, kind, me],
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

  async function run(on: string, request: unknown, command: (key: string) => Promise<RunRequest>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      await command(keyFor(on, JSON.stringify(request)));
      pending.current.delete(on);
      return true;
    } catch (error) {
      // Only an unanswered request keeps its key, so trying again repeats the same one.
      if (!(error instanceof RunRequestsError && error.code === "unavailable")) pending.current.delete(on);
      setFailed({ on, message: runRequestsErrorMessage(error), problems: error instanceof RunRequestsError ? error.problems : [] });
      return false;
    } finally {
      const fresh = await fetchList();
      if (fresh.state === "ready") setLoad(fresh);
      setBusy(null);
    }
  }

  const who = (staffId: string) => (staffId === me ? "you" : (names[staffId] ?? "a member of staff"));
  const id = `run-again-${kind}`;
  const items = load.state === "ready" ? load.items : [];
  const open = items.filter((item) => item.status === "proposed");
  const shown = [...open, ...items.filter((item) => item.status !== "proposed").slice(0, DECIDED_SHOWN)];

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex max-w-[640px] flex-col gap-1">
          <h2 id={id} className="m-0 text-[17px] font-extrabold">
            {words.title}
          </h2>
          <p className="m-0 text-sm leading-normal text-muted">
            {words.what} One person asks, someone else approves.
          </p>
        </div>
        {canChange && !writing && load.state === "ready" ? (
          <Button
            variant="secondary"
            onClick={() => {
              setWriting(true);
              setFailed(null);
            }}
          >
            {words.button}
          </Button>
        ) : null}
      </div>
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading requests
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex flex-col items-start gap-2">
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
        </div>
      ) : (
        <>
          {writing ? (
            <AskForm
              kind={kind}
              today={today}
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
          {shown.length === 0 && !writing ? <p className="m-0 text-md text-muted">{words.empty}</p> : null}
          {shown.map((item) => (
            <RequestCard
              key={`${item.id}:${item.status}`}
              item={item}
              approveText={words.approve}
              mine={item.proposedBy === me}
              canChange={canChange}
              who={who}
              busy={busy === item.id}
              locked={busy !== null}
              error={failed?.on === item.id ? failed.message : null}
              onApprove={() => run(item.id, "approve", (key) => api.approve(key, item.id))}
              onDecline={(note) => run(item.id, { note }, (key) => api.decline(key, item.id, note))}
              onWithdraw={() => run(item.id, "withdraw", (key) => api.withdraw(key, item.id))}
            />
          ))}
        </>
      )}
    </section>
  );
}

function daysBefore(day: string, days: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

interface AskFormProps {
  kind: RunKind;
  today: string;
  busy: boolean;
  error: string | null;
  refused: readonly RunProblem[];
  onCancel: () => void;
  onSubmit: (input: NewRunRequest) => Promise<boolean>;
}

function AskForm({ kind, today, busy, error, refused, onCancel, onSubmit }: AskFormProps) {
  // The interest run asks for a day, yesterday by default. The maturity run is always today's.
  const [runDate, setRunDate] = useState(kind === "interest" ? daysBefore(today, 1) : today);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const missing: RunProblem[] = [
    ...(runDate ? [] : [{ field: "run_date" as const, code: "required" }]),
    ...(reason.trim() ? [] : [{ field: "reason" as const, code: "required" }]),
    ...(reason.length > REASON_LIMIT ? [{ field: "reason" as const, code: "too_long" }] : []),
  ];
  const shown = [...(tried ? missing : missing.filter((p) => p.code !== "required")), ...refused];
  const codes = (field: RunProblem["field"]) => [...new Set(shown.filter((p) => p.field === field).map((p) => p.code))];
  const label = kind === "interest" ? "Ask for an interest run again" : "Ask for today's maturity run again";

  return (
    <form
      aria-label={label}
      className="flex flex-col gap-3 border-t border-divider pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (missing.length > 0) return;
        void onSubmit({ kind, runDate, reason: reason.trim() });
      }}
    >
      {kind === "interest" ? (
        <label className="flex max-w-[260px] flex-col gap-1.5 text-sm font-bold">
          Day to run again
          <span className="text-xs font-medium text-muted">Today or any of the last 31 days.</span>
          <input
            type="date"
            value={runDate}
            min={daysBefore(today, 31)}
            max={today}
            aria-invalid={codes("run_date").length > 0}
            onChange={(event) => setRunDate(event.target.value)}
            className={INPUT}
          />
          {codes("run_date").map((code) => (
            <span key={code} className="text-xs font-bold text-danger">
              {runProblemText(code)}
            </span>
          ))}
        </label>
      ) : (
        <p className="m-0 text-md">
          For <span className="font-bold">{formatDay(today)}</span>, today.
        </p>
      )}
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        <span className="flex justify-between gap-2">
          Why?
          <span className={cn("text-xs font-semibold", reason.length > REASON_LIMIT ? "text-danger" : "text-muted")}>
            {reason.length} of {REASON_LIMIT}
          </span>
        </span>
        <span className="text-xs font-medium text-muted">For the person who approves it and the audit trail. Say what went wrong and that it is fixed.</span>
        <textarea rows={2} value={reason} aria-invalid={codes("reason").length > 0} onChange={(event) => setReason(event.target.value)} className={INPUT} />
        {codes("reason").map((code) => (
          <span key={code} className="text-xs font-bold text-danger">
            {runProblemText(code)}
          </span>
        ))}
      </label>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
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

interface RequestCardProps {
  item: RunRequest;
  approveText: string;
  mine: boolean;
  canChange: boolean;
  who: (staffId: string) => string;
  busy: boolean;
  locked: boolean;
  error: string | null;
  onApprove: () => Promise<boolean>;
  onDecline: (note: string) => Promise<boolean>;
  onWithdraw: () => Promise<boolean>;
}

function RequestCard({ item, approveText, mine, canChange, who, busy, locked, error, onApprove, onDecline, onWithdraw }: RequestCardProps) {
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const status = STATUS[item.status];
  const waiting = item.status === "proposed";
  const title = `${item.kind === "interest" ? "Interest run" : "Maturity run"} for ${formatDay(item.runDate)}`;
  const decided = item.status === "declined" ? "Declined by " : item.status === "withdrawn" ? "Withdrawn by " : "Approved by ";

  return (
    <article aria-label={title} className="flex flex-col gap-2 border-t border-divider pt-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="m-0 text-md font-extrabold">{title}</h3>
        <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold", status.className)}>{status.label}</span>
      </div>
      <dl className="m-0 flex flex-col gap-1 text-sm text-muted">
        <div>
          <dt className="inline">Asked by </dt>
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
      {waiting && canChange && !mine && !declining ? (
        <div className="flex flex-col gap-2">
          <p className="m-0 text-sm text-muted">{approveText}</p>
          <div className="flex flex-wrap gap-2">
            <Button disabled={locked} onClick={() => void onApprove()}>
              {busy ? "Saving" : "Approve"}
            </Button>
            <Button variant="secondary" disabled={locked} onClick={() => setDeclining(true)}>
              Decline
            </Button>
          </div>
        </div>
      ) : null}
      {waiting && declining ? (
        <form
          aria-label="Decline the request"
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setTried(true);
            if (note.trim().length === 0) return;
            void onDecline(note.trim());
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            Why not?
            <textarea value={note} rows={2} aria-invalid={tried && note.trim().length === 0} onChange={(event) => setNote(event.target.value)} className={INPUT} />
          </label>
          {tried && note.trim().length === 0 ? <p className="m-0 text-xs font-bold text-danger">Say why, so the person who asked knows.</p> : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={locked}>
              {busy ? "Saving" : "Decline it"}
            </Button>
            <Button variant="secondary" disabled={locked} onClick={() => setDeclining(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </article>
  );
}
