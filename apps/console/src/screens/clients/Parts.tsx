import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import type { ClientPage, Decision, Lock, LockKind } from "./api.ts";
import {
  destinationLabel,
  destinationStatus,
  formatAccountNumber,
  formatAmount,
  formatDay,
  formatTime,
  humanize,
  LOCK_STATUS,
  owedLabel,
  secondSteps,
  STATUS_LABELS,
  totals,
} from "./labels.ts";

export const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";

const STATUS_TONE: Record<ClientPage["account"]["status"], string> = {
  unclaimed: "bg-surface-sunken text-muted",
  active: "bg-success-surface text-success",
  locked: "bg-danger-surface text-danger",
  closed: "bg-surface-sunken text-muted",
};

function Card({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section aria-label={title} className={cn("flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-surface p-4", className)}>
      <h3 className="m-0 text-[17px] font-extrabold">{title}</h3>
      {children}
    </section>
  );
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="m-0 grid gap-x-4 gap-y-2 text-sm [grid-template-columns:max-content_minmax(0,1fr)]">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="font-bold text-muted">{label}</dt>
          <dd className="m-0 break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ClientHeader({ page }: { page: ClientPage }) {
  const { account } = page;
  const number = page.identityCheck?.accountNumber;
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="m-0 text-2xl font-extrabold">{page.verifiedName ?? "Name not verified yet"}</h2>
        <span className={cn("rounded-pill px-2.5 py-0.5 text-xs font-bold", STATUS_TONE[account.status])}>{STATUS_LABELS[account.status]}</span>
      </div>
      <p className="m-0 text-md text-muted">
        {number ? (
          <>
            Account number <strong className="text-ink tabular-nums">{formatAccountNumber(number)}</strong> ·{" "}
          </>
        ) : null}
        Client ID <span className="font-mono">{account.id}</span> · Joined {formatTime(account.createdAt)}
      </p>
      {account.status === "locked" ? (
        <p className="m-0 max-w-[720px] rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
          This account is locked. The client cannot sign in, and every session they had ended when it was locked.
        </p>
      ) : null}
      {account.status === "unclaimed" ? (
        <p className="m-0 max-w-[720px] rounded-md bg-surface-sunken px-3 py-2.5 text-sm">
          Brought over from Finvest 1.0. The client has not set up a sign-in yet, so there is nothing to lock.
        </p>
      ) : null}
    </div>
  );
}

export function ClientDetails({ page }: { page: ClientPage }) {
  const { account, identityCheck } = page;
  const sums = totals(page.owed);
  return (
    <div className="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(320px,1fr))]">
      <Card title="Account">
        <Facts
          rows={[
            ["Mobile", <span className="tabular-nums">{account.phone}</span>],
            ["Email", account.email ?? "None given"],
            ["Second step", secondSteps(account)],
            ["Last signed in", account.lastSignedInAt ? formatTime(account.lastSignedInAt) : "Never"],
            ...(account.restrictedUntil ? ([["Sign-in held back until", formatTime(account.restrictedUntil)]] as [string, ReactNode][]) : []),
          ]}
        />
      </Card>
      <Card title="Identity check">
        {identityCheck ? (
          <Facts
            rows={[
              ["Status", humanize(identityCheck.status)],
              ["Submitted", identityCheck.submittedAt ? formatTime(identityCheck.submittedAt) : "Not yet"],
              ["Decided", identityCheck.decidedAt ? formatTime(identityCheck.decidedAt) : "Not yet"],
            ]}
          />
        ) : (
          <p className="m-0 text-sm text-muted">Not started.</p>
        )}
      </Card>
      <Card title="What Finvest holds for them">
        {page.owed.length === 0 ? (
          <p className="m-0 text-sm text-muted">Nothing today.</p>
        ) : (
          <>
            <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
              {page.owed.map((item) => (
                <li key={`${item.accountCode}:${item.accountReference ?? ""}:${item.currency}`} className="flex items-baseline justify-between gap-3">
                  <span>
                    {owedLabel(item)}
                    {item.accountReference ? <span className="text-muted"> · {item.accountReference}</span> : null}
                  </span>
                  <span className="font-bold whitespace-nowrap tabular-nums">{formatAmount(item.minor, item.currency)}</span>
                </li>
              ))}
            </ul>
            {sums.map((sum) => (
              <p key={sum.currency} className="m-0 flex justify-between border-t border-border pt-2 text-sm font-extrabold">
                <span>Total</span>
                <span className="tabular-nums">{formatAmount(sum.minor, sum.currency)}</span>
              </p>
            ))}
            <p className="m-0 text-xs text-muted">From the ledger, at the end of today.</p>
          </>
        )}
      </Card>
      <Card title="Payout accounts">
        {page.destinations.length === 0 ? (
          <p className="m-0 text-sm text-muted">None added.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-3 p-0 text-sm">
            {page.destinations.map((item) => (
              <li key={item.id} className="flex flex-col gap-0.5">
                <span className="font-bold">
                  {destinationLabel(item)} <span className="font-mono font-medium">{item.maskedAccount}</span>
                </span>
                <span className="text-muted">
                  {item.holderName} · {destinationStatus(item)} · added {formatTime(item.createdAt)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {page.conversation || page.complaints ? (
        <Card title="Help and complaints">
          {page.conversation ? (
            <p className="m-0 text-sm">
              <strong>Help chat:</strong> {page.conversation.topic}, {humanize(page.conversation.status).toLowerCase()}.{" "}
              {page.conversation.waitingSince ? `Waiting for a reply since ${formatTime(page.conversation.waitingSince)}.` : `Last message ${formatTime(page.conversation.lastMessageAt)}.`}
            </p>
          ) : null}
          {page.complaints ? (
            page.complaints.length === 0 ? (
              <p className="m-0 text-sm text-muted">No complaints.</p>
            ) : (
              <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
                {page.complaints.map((item) => (
                  <li key={item.id} className="flex flex-col gap-0.5">
                    <span className="font-bold">
                      {item.reference} · {item.summary}
                    </span>
                    <span className={cn("text-muted", item.overdue && "font-bold text-danger")}>
                      {humanize(item.status)} · due {formatDay(item.dueOn)}
                      {item.overdue ? " · overdue" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

export function ReasonForm({
  label,
  hint,
  submit,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  label: string;
  hint: string;
  submit: string;
  busy: boolean;
  error: string | null;
  onSubmit: (reason: string) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (reason.trim()) onSubmit(reason.trim());
      }}
    >
      <label className="flex flex-col gap-1.5 text-sm font-bold">
        {label}
        <span className="text-xs font-medium text-muted">{hint}</span>
        <textarea value={reason} rows={2} maxLength={500} aria-invalid={tried && !reason.trim()} onChange={(event) => setReason(event.target.value)} className={INPUT} />
        {tried && !reason.trim() ? <span className="text-xs font-bold text-danger">Write a short reason for the audit trail.</span> : null}
      </label>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Sending" : submit}
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

const KIND_WORDS: Record<LockKind, { title: string; propose: string; approve: string }> = {
  lock: { title: "Lock the account", propose: "Propose a lock", approve: "Approve and lock" },
  unlock: { title: "Unlock the account", propose: "Propose an unlock", approve: "Approve and unlock" },
};

/** Who may lock or unlock, what is waiting, and what was decided before. */
export function LockSection({
  page,
  busy,
  error,
  onPropose,
  onDecide,
}: {
  page: ClientPage;
  busy: boolean;
  error: string | null;
  onPropose: (kind: LockKind, reason: string) => Promise<boolean>;
  onDecide: (lock: Lock, decision: Decision, reason: string) => Promise<boolean>;
}) {
  const [form, setForm] = useState<"propose" | Decision | null>(null);
  const waiting = page.locks.find((lock) => lock.status === "proposed");
  // A locked account was frozen by the client when their latest freeze came after the latest
  // approved lock. Both lists are newest first.
  const latestFreeze = page.freezes[0];
  const latestLock = page.locks.find((lock) => lock.kind === "lock" && lock.status === "approved");
  const frozenBy =
    latestFreeze && (!latestLock?.decidedAt || Date.parse(latestFreeze.frozenAt) > Date.parse(latestLock.decidedAt))
      ? latestFreeze
      : null;
  const past = page.locks.filter((lock) => lock.status !== "proposed");
  const status = page.account.status;
  const kind: LockKind | null = status === "active" ? "lock" : status === "locked" ? "unlock" : null;
  const close = (done: boolean) => {
    if (done) setForm(null);
  };

  return (
    <section aria-labelledby="locks" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id="locks" className="m-0 text-xl font-extrabold">
          Lock and unlock
        </h2>
        <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
          A lock stops the client signing in and ends every session they have. One person proposes it with a reason and a different person approves it. A client can also freeze their own account from the app; unfreezing it is an unlock.
        </p>
      </div>

      {status === "locked" && frozenBy ? (
        <article aria-label="Frozen by the client" className="flex max-w-[720px] flex-col gap-2 rounded-xl border border-border bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-[17px] font-extrabold">Frozen by the client</h3>
            <span className="rounded-pill bg-surface-sunken px-2 py-0.5 text-2xs font-bold">{formatTime(frozenBy.frozenAt)}</span>
          </div>
          <p className="m-0 text-md">{frozenBy.reason ? `“${frozenBy.reason}”` : "They gave no reason."}</p>
          <p className="m-0 text-sm text-muted">
            The client froze their own account in the app, which ended every session. Before proposing an unlock, check it is really them, for example by calling back on the number on file.
          </p>
        </article>
      ) : null}

      {waiting ? (
        <article aria-label={`${KIND_WORDS[waiting.kind].title}, waiting`} className="flex max-w-[720px] flex-col gap-3 rounded-xl border border-border bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-[17px] font-extrabold">{KIND_WORDS[waiting.kind].title}</h3>
            <span className="rounded-pill bg-warning-surface px-2 py-0.5 text-2xs font-bold text-warning-text">Waiting for a second person</span>
          </div>
          <p className="m-0 text-md">“{waiting.reason}”</p>
          <p className="m-0 text-sm text-muted">
            Proposed by {waiting.yours ? "you" : waiting.proposedByName}, {formatTime(waiting.proposedAt)}
          </p>
          {!page.canLock ? (
            <p className="m-0 text-sm text-muted">Your role cannot decide locks.</p>
          ) : form === "approved" || form === "rejected" || form === "withdrawn" ? (
            <ReasonForm
              label={form === "approved" ? "Why approve?" : form === "rejected" ? "Why reject?" : "Why withdraw?"}
              hint="This goes in the audit trail with your name."
              submit={form === "approved" ? KIND_WORDS[waiting.kind].approve : form === "rejected" ? "Reject" : "Withdraw"}
              busy={busy}
              error={error}
              onSubmit={(reason) => void onDecide(waiting, form, reason).then(close)}
              onCancel={() => setForm(null)}
            />
          ) : (
            <>
              {error ? (
                <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
                  {error}
                </p>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {waiting.yours ? (
                  <Button variant="secondary" onClick={() => setForm("withdrawn")}>
                    Withdraw
                  </Button>
                ) : (
                  <>
                    <Button onClick={() => setForm("approved")}>Approve</Button>
                    <Button variant="secondary" onClick={() => setForm("rejected")}>
                      Reject
                    </Button>
                  </>
                )}
              </div>
              {waiting.yours ? <p className="m-0 text-xs text-muted">You proposed this, so someone else approves it.</p> : null}
            </>
          )}
        </article>
      ) : kind && page.canLock ? (
        form === "propose" ? (
          <div className="flex max-w-[720px] flex-col gap-3 rounded-xl border border-border bg-surface p-4">
            <h3 className="m-0 text-[17px] font-extrabold">{KIND_WORDS[kind].title}</h3>
            <ReasonForm
              label="Why?"
              hint="The second person reads this, and it goes in the audit trail."
              submit="Send for approval"
              busy={busy}
              error={error}
              onSubmit={(reason) => void onPropose(kind, reason).then(close)}
              onCancel={() => setForm(null)}
            />
          </div>
        ) : (
          <div className="flex flex-col items-start gap-2">
            {error ? (
              <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
                {error}
              </p>
            ) : null}
            <Button onClick={() => setForm("propose")}>
              {KIND_WORDS[kind].propose}
            </Button>
          </div>
        )
      ) : !page.canLock ? (
        <p className="m-0 max-w-[720px] rounded-md bg-surface-sunken px-3 py-2.5 text-sm text-muted">Your role can see this client. Locking needs operations, compliance or an admin.</p>
      ) : null}

      {past.length > 0 ? (
        <ul aria-label="Earlier locks and unlocks" className="m-0 flex max-w-[720px] list-none flex-col gap-0 rounded-xl border border-border bg-surface px-4 py-1">
          {past.map((lock) => (
            <li key={lock.id} className="flex flex-col gap-1 border-b border-border py-3 text-sm last:border-b-0">
              <span className="font-bold">
                {lock.kind === "lock" ? "Lock" : "Unlock"} · {LOCK_STATUS[lock.status]}
                {lock.decidedAt ? `, ${formatTime(lock.decidedAt)}` : ""}
              </span>
              <span>
                Proposed by {lock.proposedByName}: “{lock.reason}”
              </span>
              {lock.decidedByName ? (
                <span className="text-muted">
                  {lock.status === "withdrawn" ? "Withdrawn" : `${LOCK_STATUS[lock.status]} by ${lock.decidedByName}`}
                  {lock.decisionReason ? `: “${lock.decisionReason}”` : ""}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
