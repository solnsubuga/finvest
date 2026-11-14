import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import type { ClientCard, Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import { formatDay, formatStamp } from "../../products/format.ts";
import {
  ReissuesError,
  reissueProblemText,
  reissuesErrorMessage,
  type NewReissue,
  type Reissue,
  type ReissueField,
  type ReissueProblem,
  type ReissuesApi,
} from "./api.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const REASON_LIMIT = 500;
const MAX_DAYS = 31;
// Finished requests stay in view for a while, so the person who asked sees how it went.
const DECIDED_SHOWN = 3;

const STATUS: Record<Reissue["status"], { label: string; className: string }> = {
  proposed: { label: "Waiting for a second person", className: "bg-warning-surface text-warning-text" },
  approved: { label: "Approved, sending", className: "bg-success-surface text-success" },
  done: { label: "Sent again", className: "bg-success-surface text-success" },
  declined: { label: "Declined", className: "bg-surface-sunken text-muted" },
  withdrawn: { label: "Withdrawn", className: "bg-surface-sunken text-muted" },
};

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; items: readonly Reissue[] };
type Failure = { on: string; message: string; problems: readonly ReissueProblem[] };

interface ReissuesProps {
  api: ReissuesApi;
  directory: Directory;
  /** The signed-in member of staff. */
  me: string;
  /** `client_statements.send`: ask and decide. The API checks it again. */
  canChange: boolean;
  /** The Kampala business day. */
  today: string;
  newKey?: () => string;
}

function daysBefore(day: string, days: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

const plural = (n: number, one: string, many: string) => `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;

function days(from: string, to: string): string {
  return from === to ? formatDay(from) : `${formatDay(from)} to ${formatDay(to)}`;
}

/** Emailing investment notes again: the same stored notes, with the same numbers. Two people. */
export function Reissues({ api, directory, me, canChange, today, newKey = () => crypto.randomUUID() }: ReissuesProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [writing, setWriting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<Failure | null>(null);
  const [names, setNames] = useState<Record<string, string | null>>({});
  const [clients, setClients] = useState<Record<string, string | null>>({});
  const asked = useRef(new Set<string>());
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
          for (const clientId of items.flatMap((item) => (item.clientId ? [item.clientId] : []))) {
            if (asked.current.has(clientId)) continue;
            asked.current.add(clientId);
            void directory.client(clientId).then(
              (card) => setClients((known) => ({ ...known, [clientId]: card?.name ?? null })),
              () => setClients((known) => ({ ...known, [clientId]: null })),
            );
          }
          return { state: "ready", items };
        },
        (error: unknown) => ({ state: "failed", message: reissuesErrorMessage(error) }),
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

  async function run(on: string, request: unknown, command: (key: string) => Promise<Reissue>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      await command(keyFor(on, JSON.stringify(request)));
      pending.current.delete(on);
      return true;
    } catch (error) {
      // Only an unanswered request keeps its key, so trying again repeats the same one.
      if (!(error instanceof ReissuesError && error.code === "unavailable")) pending.current.delete(on);
      setFailed({ on, message: reissuesErrorMessage(error), problems: error instanceof ReissuesError ? error.problems : [] });
      return false;
    } finally {
      const fresh = await fetchList();
      if (fresh.state === "ready") setLoad(fresh);
      setBusy(null);
    }
  }

  const who = (staffId: string) => (staffId === me ? "you" : (names[staffId] ?? "a member of staff"));
  const whose = (item: Reissue) => (item.clientId ? (clients[item.clientId] ?? "one client") : "every client");
  const items = load.state === "ready" ? load.items : [];
  const open = items.filter((item) => item.status === "proposed");
  const shown = [...open, ...items.filter((item) => item.status !== "proposed").slice(0, DECIDED_SHOWN)];

  return (
    <section aria-labelledby="reissue-notes" className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex max-w-[640px] flex-col gap-1">
          <h2 id="reissue-notes" className="m-0 text-[17px] font-extrabold">
            Reissue transaction notes
          </h2>
          <p className="m-0 text-sm leading-normal text-muted">
            Email investment notes again when a client lost the email or a day's emails went wrong. Each client gets the same note, with the same number and
            figures, locked with their date of birth as before. One person asks, someone else approves.
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
            Reissue notes
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
              api={api}
              directory={directory}
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
          {shown.length === 0 && !writing ? <p className="m-0 text-md text-muted">No notes have been sent again.</p> : null}
          {shown.map((item) => (
            <RequestCard
              key={`${item.id}:${item.status}`}
              item={item}
              whose={whose(item)}
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

type Count = { state: "idle" } | { state: "counting" } | { state: "counted"; notes: number } | { state: "failed" };

interface AskFormProps {
  api: ReissuesApi;
  directory: Directory;
  today: string;
  busy: boolean;
  error: string | null;
  refused: readonly ReissueProblem[];
  onCancel: () => void;
  onSubmit: (input: NewReissue) => Promise<boolean>;
}

function AskForm({ api, directory, today, busy, error, refused, onCancel, onSubmit }: AskFormProps) {
  const [scope, setScope] = useState<"one" | "every">("one");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<ClientCard[]>([]);
  const [client, setClient] = useState<ClientCard | null>(null);
  // One client's notes from the last month by default. Every client's for one day.
  const [issuedFrom, setIssuedFrom] = useState(daysBefore(today, MAX_DAYS - 1));
  const [issuedTo, setIssuedTo] = useState(today);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  // The last count Finvest gave, for the scope it was asked about. Null notes: it failed.
  const [counted, setCounted] = useState<{ key: string; notes: number | null } | null>(null);

  useEffect(() => {
    if (scope !== "one" || client) return;
    let current = true;
    void directory.findClients(query).then(
      (cards) => current && setFound(cards.filter((card) => card.verified)),
      () => current && setFound([]),
    );
    return () => {
      current = false;
    };
  }, [directory, query, client, scope]);

  const daysWrong = !issuedFrom || !issuedTo || issuedFrom > issuedTo || issuedTo > today || daysBetween(issuedFrom, issuedTo) >= MAX_DAYS;
  const clientId = scope === "one" ? (client?.clientId ?? null) : null;
  const ready = !daysWrong && (scope === "every" || clientId !== null);

  const scopeKey = ready ? JSON.stringify([clientId, issuedFrom, issuedTo]) : null;
  useEffect(() => {
    if (scopeKey === null) return;
    let current = true;
    void api.count({ clientId, issuedFrom, issuedTo }).then(
      (notes) => current && setCounted({ key: scopeKey, notes }),
      () => current && setCounted({ key: scopeKey, notes: null }),
    );
    return () => {
      current = false;
    };
  }, [api, scopeKey, clientId, issuedFrom, issuedTo]);
  const count: Count =
    scopeKey === null
      ? { state: "idle" }
      : counted?.key !== scopeKey
        ? { state: "counting" }
        : counted.notes === null
          ? { state: "failed" }
          : { state: "counted", notes: counted.notes };

  const missing: ReissueProblem[] = [
    ...(scope === "one" && !client ? [{ field: "client_id" as const, code: "required" }] : []),
    ...(!issuedFrom ? [{ field: "issued_from" as const, code: "required" }] : []),
    ...(!issuedTo ? [{ field: "issued_to" as const, code: "required" }] : []),
    ...(issuedFrom && issuedTo && issuedFrom > issuedTo ? [{ field: "issued_from" as const, code: "after_issued_to" }] : []),
    ...(issuedTo && issuedTo > today ? [{ field: "issued_to" as const, code: "not_yet" }] : []),
    ...(issuedFrom && issuedTo && issuedFrom <= issuedTo && daysBetween(issuedFrom, issuedTo) >= MAX_DAYS ? [{ field: "issued_to" as const, code: "too_long" }] : []),
    ...(reason.trim() ? [] : [{ field: "reason" as const, code: "required" }]),
    ...(reason.length > REASON_LIMIT ? [{ field: "reason" as const, code: "too_long" }] : []),
  ];
  const shown = [...(tried ? missing : missing.filter((p) => p.code !== "required")), ...refused];
  const codes = (field: ReissueField) => [...new Set(shown.filter((p) => p.field === field).map((p) => p.code))];
  const errors = (field: ReissueField) =>
    codes(field).map((code) => (
      <span key={code} className="text-xs font-bold text-danger">
        {reissueProblemText(code, field)}
      </span>
    ));
  const nothing = count.state === "counted" && count.notes === 0;

  return (
    <form
      aria-label="Reissue transaction notes"
      className="flex flex-col gap-3 border-t border-divider pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (missing.length > 0 || nothing) return;
        void onSubmit({ clientId, issuedFrom, issuedTo, reason: reason.trim() });
      }}
    >
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 p-0 text-sm font-bold">Whose notes</legend>
        <div className="flex flex-wrap gap-2">
          {(
            [
              ["one", "One client"],
              ["every", "Every client"],
            ] as const
          ).map(([value, label]) => (
            <label
              key={value}
              className={cn(
                "flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3.5 text-sm font-semibold",
                scope === value ? "border-ink bg-paper" : "border-border-strong",
              )}
            >
              <input type="radio" name="reissue-scope" value={value} checked={scope === value} onChange={() => setScope(value)} />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      {scope === "one" ? (
        client ? (
          <div className="flex items-center justify-between gap-2 rounded-lg bg-paper px-3.5 py-3 text-sm">
            <span>
              <strong>{client.name}</strong>
              <span className="text-muted">{client.accountNumber ? `, account ${client.accountNumber}` : ""}</span>
            </span>
            <Button variant="secondary" className="h-9 px-3" onClick={() => setClient(null)}>
              Change
            </Button>
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            <label className="flex flex-col gap-1.5 text-sm font-bold">
              Client
              <Input
                value={query}
                placeholder="Name or account number"
                aria-invalid={codes("client_id").length > 0}
                onChange={(event) => setQuery(event.target.value)}
                className="h-11 text-md"
              />
            </label>
            {found.length > 0 ? (
              <ul aria-label="Matching clients" className="m-0 flex list-none flex-col gap-1 p-0">
                {found.map((card) => (
                  <li key={card.clientId}>
                    <button type="button" onClick={() => setClient(card)} className="w-full cursor-pointer rounded-md px-3 py-2 text-left text-sm hover:bg-paper">
                      <strong>{card.name}</strong>
                      <span className="text-muted">{card.accountNumber ? `, ${card.accountNumber}` : ""}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : query.trim().length >= 2 ? (
              <p className="m-0 text-xs text-muted">No client matches that.</p>
            ) : null}
            {errors("client_id")}
          </div>
        )
      ) : null}
      <div className="flex flex-wrap gap-3">
        <label className="flex min-w-[180px] flex-1 flex-col gap-1.5 text-sm font-bold sm:max-w-[240px]">
          Notes issued from
          <input type="date" value={issuedFrom} max={today} aria-invalid={codes("issued_from").length > 0} onChange={(event) => setIssuedFrom(event.target.value)} className={INPUT} />
          {errors("issued_from")}
        </label>
        <label className="flex min-w-[180px] flex-1 flex-col gap-1.5 text-sm font-bold sm:max-w-[240px]">
          to
          <input type="date" value={issuedTo} max={today} aria-invalid={codes("issued_to").length > 0} onChange={(event) => setIssuedTo(event.target.value)} className={INPUT} />
          {errors("issued_to")}
        </label>
      </div>
      <p className="-mt-1 m-0 text-xs text-muted">At most 31 days, both included.</p>
      <p role="status" className={cn("m-0 text-md", nothing ? "font-semibold text-danger" : "text-ink")}>
        {count.state === "counting"
          ? "Counting notes"
          : count.state === "counted"
            ? count.notes === 0
              ? "No notes were issued on these days, so there is nothing to send."
              : `${plural(count.notes, "note", "notes")} would be emailed again.`
            : count.state === "failed"
              ? "Could not count the notes. You can still send it for approval."
              : scope === "one"
                ? "Choose the client to see how many notes this covers."
                : ""}
      </p>
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        <span className="flex justify-between gap-2">
          Why?
          <span className={cn("text-xs font-semibold", reason.length > REASON_LIMIT ? "text-danger" : "text-muted")}>
            {reason.length} of {REASON_LIMIT}
          </span>
        </span>
        <span className="text-xs font-medium text-muted">For the person who approves it and the audit trail. Say what went wrong, or how the client asked.</span>
        <textarea rows={2} value={reason} aria-invalid={codes("reason").length > 0} onChange={(event) => setReason(event.target.value)} className={INPUT} />
        {errors("reason")}
      </label>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || nothing}>
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
  item: Reissue;
  whose: string;
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

function outcome(item: Reissue): string | null {
  const { sent, skipped, failed, queued } = item.copies;
  if (item.status !== "approved" && item.status !== "done") return null;
  const parts = [
    `${sent.toLocaleString("en-GB")} sent`,
    ...(queued ? [`${queued.toLocaleString("en-GB")} still sending`] : []),
    ...(skipped ? [`${skipped.toLocaleString("en-GB")} not sent: no proved email address, a blocked address or no date of birth on record`] : []),
    ...(failed ? [`${failed.toLocaleString("en-GB")} refused by the email service`] : []),
  ];
  return `${parts.join("; ")}.`;
}

function RequestCard({ item, whose, mine, canChange, who, busy, locked, error, onApprove, onDecline, onWithdraw }: RequestCardProps) {
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const status = STATUS[item.status];
  const waiting = item.status === "proposed";
  const title = `Notes issued ${days(item.issuedFrom, item.issuedTo)}, for ${whose}`;
  const decided = item.status === "declined" ? "Declined by " : item.status === "withdrawn" ? "Withdrawn by " : "Approved by ";
  const went = outcome(item);

  return (
    <article aria-label={title} className="flex flex-col gap-2 border-t border-divider pt-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="m-0 text-md font-extrabold">{title}</h3>
        <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold", status.className)}>{status.label}</span>
      </div>
      <p className="m-0 text-sm">{plural(item.noteCount, "note", "notes")}</p>
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
      {went ? <p className="m-0 text-sm">{went}</p> : null}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      {waiting && !canChange ? <p className="m-0 text-sm text-muted">Someone who can send documents to clients approves it.</p> : null}
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
          <p className="m-0 text-sm text-muted">Approving emails each note again straight away. Check the reason and the days.</p>
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
