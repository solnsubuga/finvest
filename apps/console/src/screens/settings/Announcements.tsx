import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import type { Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import {
  ANNOUNCEMENT_LIMITS,
  AnnouncementsError,
  type AnnouncementReach,
  announcementProblemText,
  announcementsErrorMessage,
  checkAnnouncement,
  type Announcement,
  type AnnouncementDecision,
  type AnnouncementField,
  type AnnouncementProblem,
  type AnnouncementsApi,
  type NewAnnouncement,
} from "./announcements.ts";
import { stamp } from "./labels.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const FIELDS: readonly { field: AnnouncementField; label: string; hint: string; rows: number }[] = [
  { field: "title", label: "Title", hint: "In the app, push notifications and the email subject.", rows: 1 },
  { field: "message", label: "Message", hint: "In the app, push notifications and emails. By SMS it starts with Finvest.", rows: 3 },
  { field: "reason", label: "Why send it?", hint: "Staff only, for the person who approves it and the audit trail.", rows: 2 },
];
const EMPTY: NewAnnouncement = { title: "", message: "", reason: "" };

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; items: Announcement[] };

interface AnnouncementsSectionProps {
  api: AnnouncementsApi;
  directory: Directory;
  /** `announcements.send`: propose and decide. The API checks it again. */
  canChange: boolean;
}

/** Words sent to every active client, agreed by two people. */
export function AnnouncementsSection({ api, directory, canChange }: AnnouncementsSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [writing, setWriting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ on: string; message: string; problems: readonly AnnouncementProblem[] } | null>(null);
  const [names, setNames] = useState<Record<string, string | null>>({});
  const asked = useRef(new Set<string>());
  // Who approving would reach. Null while unknown or if it could not be read.
  const [reach, setReach] = useState<AnnouncementReach | null>(null);

  const fetchList = useCallback(
    (): Promise<Load> =>
      api.list().then(
        (items) => {
          for (const staffId of items.flatMap((item) => [item.proposedBy, ...(item.decidedBy ? [item.decidedBy] : [])])) {
            if (asked.current.has(staffId)) continue;
            asked.current.add(staffId);
            void directory.staffName(staffId).then(
              (name) => setNames((known) => ({ ...known, [staffId]: name })),
              () => setNames((known) => ({ ...known, [staffId]: null })),
            );
          }
          return { state: "ready", items };
        },
        (error: unknown) => ({ state: "failed", message: announcementsErrorMessage(error) }),
      ),
    [api, directory],
  );

  const someoneWaits = load.state === "ready" && load.items.some((item) => item.status === "proposed" && !item.yours);
  useEffect(() => {
    if (!canChange || !someoneWaits) return;
    let current = true;
    api.reach().then(
      (found) => current && setReach(found),
      () => current && setReach(null),
    );
    return () => {
      current = false;
    };
  }, [api, canChange, someoneWaits]);

  useEffect(() => {
    let current = true;
    void fetchList().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchList]);

  async function run(on: string, command: () => Promise<Announcement>): Promise<boolean> {
    setBusy(true);
    setFailed(null);
    try {
      await command();
      return true;
    } catch (error) {
      setFailed({
        on,
        message: announcementsErrorMessage(error),
        problems: error instanceof AnnouncementsError ? error.problems : [],
      });
      return false;
    } finally {
      // The list always shows what Finvest now holds, after a change or a refusal.
      const fresh = await fetchList();
      if (fresh.state === "ready") setLoad(fresh);
      setBusy(false);
    }
  }

  const staffName = (staffId: string) => names[staffId] ?? "a member of staff";
  const waiting = load.state === "ready" ? load.items.filter((item) => item.status === "proposed").length : 0;

  return (
    <section aria-labelledby="announcements" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex max-w-[720px] flex-col gap-1">
          <h2 id="announcements" className="m-0 text-xl font-extrabold">
            Announcements
          </h2>
          <p className="m-0 text-md leading-normal text-muted">
            A message to every active client, as news from Finvest. One person proposes it and someone else approves it, which sends
            it.{" "}
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
            New announcement
          </Button>
        ) : null}
      </div>

      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading announcements
        </p>
      ) : load.state === "failed" ? (
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">Announcements could not be loaded</h3>
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
            <NewAnnouncementForm
              busy={busy}
              error={failed?.on === "new" ? failed.message : null}
              refused={failed?.on === "new" ? failed.problems : []}
              onCancel={() => {
                setWriting(false);
                setFailed(null);
              }}
              onSubmit={(input) =>
                run("new", () => api.propose(input)).then((done) => {
                  if (done) setWriting(false);
                  return done;
                })
              }
            />
          ) : null}
          {load.items.length === 0 && !writing ? (
            <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
              <h3 className="m-0 text-[17px] font-extrabold">No announcements yet</h3>
              <p className="m-0 text-md leading-normal text-muted">
                Use one for news every client should hear, such as an office closure or a new product. For one client, reply in the
                help chat instead.
              </p>
            </div>
          ) : (
            load.items.map((item) => (
              <AnnouncementCard
                key={`${item.id}:${item.status}`}
                item={item}
                canChange={canChange}
                staffName={staffName}
                reach={reach}
                busy={busy}
                error={failed?.on === item.id ? failed.message : null}
                onDecide={(decision, reason) => run(item.id, () => api.decide(item.id, decision, reason))}
              />
            ))
          )}
        </div>
      )}
    </section>
  );
}

interface NewAnnouncementFormProps {
  busy: boolean;
  error: string | null;
  refused: readonly AnnouncementProblem[];
  onCancel: () => void;
  onSubmit: (input: NewAnnouncement) => Promise<boolean>;
}

function NewAnnouncementForm({ busy, error, refused, onCancel, onSubmit }: NewAnnouncementFormProps) {
  const [draft, setDraft] = useState<NewAnnouncement>(EMPTY);
  const [tried, setTried] = useState(false);
  const live = checkAnnouncement(draft);
  // Missing words are shown once the person tries to send. Broken rules are shown as they type.
  const problems = [...live.filter((problem) => tried || problem.code !== "required"), ...(tried ? refused : [])];
  const codesFor = (field: AnnouncementField) => [...new Set(problems.filter((p) => p.field === field).map((p) => p.code))];

  return (
    <form
      aria-label="New announcement"
      className="flex flex-col gap-3 rounded-[20px] border border-border bg-surface p-5"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (live.length > 0) return;
        void onSubmit(draft);
      }}
    >
      <h3 className="m-0 text-[17px] font-extrabold">New announcement</h3>
      {FIELDS.map(({ field, label, hint, rows }) => {
        const codes = codesFor(field);
        return (
          <label key={field} className="flex flex-col gap-1.5 text-sm font-bold">
            <span className="flex justify-between gap-2">
              {label}
              {field === "reason" ? null : (
                <span className={cn("text-xs font-semibold", draft[field].length > ANNOUNCEMENT_LIMITS[field] ? "text-danger" : "text-muted")}>
                  {draft[field].length} of {ANNOUNCEMENT_LIMITS[field]}
                </span>
              )}
            </span>
            <span className="text-xs font-medium text-muted">{hint}</span>
            <textarea
              value={draft[field]}
              rows={rows}
              aria-invalid={codes.length > 0}
              onChange={(event) => setDraft({ ...draft, [field]: event.target.value })}
              className={INPUT}
            />
            {codes.map((code) => (
              <span key={code} className="text-xs font-bold text-danger">
                {announcementProblemText(code, field)}
              </span>
            ))}
          </label>
        );
      })}
      {draft.title.trim() || draft.message.trim() ? (
        <figure aria-label="As a client sees it" className="m-0 flex flex-col gap-2 rounded-[16px] bg-paper p-4">
          <p className="m-0 text-md font-bold">{draft.title}</p>
          <p className="m-0 text-sm leading-normal">{draft.message}</p>
          <p className="m-0 border-t border-divider pt-2 text-xs leading-normal text-muted">
            <span className="font-bold">SMS: </span>Finvest: {draft.message}
          </p>
        </figure>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <p className="m-0 text-xs leading-normal text-muted">Nothing is sent until someone else approves it.</p>
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

const STATUS: Record<Announcement["status"], { label: string; className: string }> = {
  proposed: { label: "Waiting for a second person", className: "bg-warning-surface text-warning-text" },
  approved: { label: "Sent", className: "bg-success-surface text-success" },
  rejected: { label: "Rejected", className: "bg-danger-surface text-danger" },
  withdrawn: { label: "Withdrawn", className: "bg-surface-sunken text-muted" },
};

interface AnnouncementCardProps {
  item: Announcement;
  canChange: boolean;
  staffName: (staffId: string) => string;
  reach: AnnouncementReach | null;
  busy: boolean;
  error: string | null;
  onDecide: (decision: AnnouncementDecision, reason: string | null) => Promise<boolean>;
}

function AnnouncementCard({ item, canChange, staffName, reach, busy, error, onDecide }: AnnouncementCardProps) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const status = STATUS[item.status];
  const who = (staffId: string) => (staffId === item.proposedBy && item.yours ? "you" : staffName(staffId));
  const decidedBy = item.decidedBy ? (item.decidedBy === item.proposedBy ? who(item.decidedBy) : staffName(item.decidedBy)) : null;
  const waiting = item.status === "proposed";

  return (
    <article aria-label={item.title} className="flex flex-col gap-2.5 rounded-[20px] border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="m-0 text-[17px] font-extrabold">{item.title}</h3>
        <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold", status.className)}>
          {item.status === "approved" ? `Sent to ${item.told.toLocaleString("en-GB")} clients` : status.label}
        </span>
      </div>
      <p className="m-0 text-md leading-normal">{item.message}</p>
      <dl className="m-0 flex flex-col gap-1 text-sm text-muted">
        <div>
          <dt className="inline">Proposed by </dt>
          <dd className="m-0 inline">
            {who(item.proposedBy)}, {stamp(item.proposedAt)}. {item.reason}
          </dd>
        </div>
        {item.decidedAt && decidedBy ? (
          <div>
            <dt className="inline">{item.status === "approved" ? "Approved by " : item.status === "rejected" ? "Rejected by " : "Withdrawn by "}</dt>
            <dd className="m-0 inline">
              {decidedBy}, {stamp(item.decidedAt)}
              {item.decisionReason ? `. ${item.decisionReason}` : ""}
            </dd>
          </div>
        ) : null}
      </dl>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      {waiting && !canChange ? (
        <p className="m-0 text-sm text-muted">Someone who can send announcements approves it.</p>
      ) : null}
      {waiting && canChange && item.yours ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted">Someone else approves it.</span>
          <Button variant="secondary" disabled={busy} onClick={() => void onDecide("withdrawn", null)}>
            Withdraw it
          </Button>
        </div>
      ) : null}
      {waiting && canChange && !item.yours && !rejecting ? (
        <div className="flex flex-col gap-2">
          <p className="m-0 text-sm text-muted">{reachSentence(reach)} It cannot be called back.</p>
          <div className="flex gap-2">
            <Button disabled={busy} onClick={() => void onDecide("approved", null)}>
              {busy ? "Saving" : "Approve and send"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setRejecting(true)}>
              Reject
            </Button>
          </div>
        </div>
      ) : null}
      {waiting && rejecting ? (
        <form
          aria-label="Reject the announcement"
          className="flex flex-col gap-2 border-t border-divider pt-3"
          onSubmit={(event) => {
            event.preventDefault();
            setTried(true);
            if (note.trim().length === 0) return;
            void onDecide("rejected", note);
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            What needs to change?
            <textarea
              value={note}
              rows={2}
              aria-invalid={tried && note.trim().length === 0}
              onChange={(event) => setNote(event.target.value)}
              className={INPUT}
            />
          </label>
          {tried && note.trim().length === 0 ? (
            <p className="m-0 text-xs font-bold text-danger">Say why, so the proposer knows what to change.</p>
          ) : null}
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Saving" : "Reject it"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setRejecting(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </article>
  );
}

const count = (value: number) => value.toLocaleString("en-GB");

/** What approving does, with who it reaches once that is known. */
export function reachSentence(reach: AnnouncementReach | null): string {
  if (!reach) return "Approving sends it to every active client straight away.";
  const clients = reach.clients === 1 ? "1 active client" : `${count(reach.clients)} active clients`;
  const extra =
    reach.sms === 0 && reach.email === 0
      ? "None of them chose SMS or email for news."
      : `${count(reach.sms)} of them also get it by SMS and ${count(reach.email)} by email, as they chose for news.`;
  return `Approving sends it straight away to ${clients}, in the app, and by push where they have it. ${extra}`;
}
