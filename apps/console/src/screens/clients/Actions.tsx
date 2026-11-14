import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button.tsx";
import {
  ActionError,
  actionErrorMessage,
  LIMIT_KEYS,
  LIMIT_LABELS,
  type ActionDecision,
  type ActionKind,
  type ActionsApi,
  type ClientAction,
  type ClientActions,
  type ClientLimits,
  type Limits,
  type PayableAccount,
  type Proposal,
  type WithdrawalOptions,
} from "./actions.ts";
import { formatAmount, formatDay, formatTime } from "./labels.ts";
import { INPUT, ReasonForm } from "./Parts.tsx";

interface Loaded {
  found: ClientActions;
  limits: ClientLimits;
  /** Only for staff who may start a withdrawal. */
  options: WithdrawalOptions | null;
}

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; data: Loaded };
type Form = { kind: ActionKind } | { decide: ClientAction; decision: ActionDecision } | null;

const KIND: Record<ActionKind, { title: string; start: string; approve: string; approved: string }> = {
  pin_reset: {
    title: "Reset the app PIN",
    start: "Reset app PIN",
    approve: "Approve and sign out of the app",
    approved: "Approved. The client is signed out of the phone app and has been told. They log in again to choose a new PIN.",
  },
  withdrawal_limits: {
    title: "Change withdrawal limits",
    start: "Change withdrawal limits",
    approve: "Approve new limits",
    approved: "Approved. The client's own limits apply to every payout from now.",
  },
  statement: {
    title: "Send a statement",
    start: "Send a statement",
    approve: "Approve and send",
    approved: "Approved. The statement is being made. It goes to the client's app and to their proved email.",
  },
  withdrawal: {
    title: "Start a withdrawal",
    start: "Start a withdrawal",
    approve: "Approve withdrawal",
    approved: "Approved. The early withdrawal is asked for, and the client has been told by SMS and email.",
  },
};

const STATUS_WORDS: Record<ClientAction["status"], string> = {
  proposed: "Waiting",
  approved: "Approved",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

/** How many decided actions the history shows. */
const HISTORY = 5;

const shillings = (value: number | null) => (value === null ? "No limit" : formatAmount(value, "UGX"));

function accountLabel(account: PayableAccount): string {
  const where = account.kind === "mobile_money" ? `${account.network ?? "Mobile money"}` : (account.bankCode ?? "Bank");
  return `${where} ${account.maskedAccount}, ${account.holderName}`;
}

function Alert({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold">
      {children}
    </p>
  );
}

/**
 * Acting for the client: reset the app PIN, change their withdrawal limits, send a statement,
 * start a withdrawal they asked for in writing. One person proposes, a different one approves.
 * Shown to every member of staff who can see the client; each button only to those whose role
 * holds its permission, and the backend checks it again.
 */
export function ActionsSection({
  api,
  clientId,
  canRefund,
  newKey = () => crypto.randomUUID(),
}: {
  api: ActionsApi;
  clientId: string;
  canRefund: boolean;
  /** A fresh idempotency key. One is chosen when a form opens and sent again on every retry of it. */
  newKey?: () => string;
}) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [form, setForm] = useState<Form>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const id = useId();
  // The key for the form open now, chosen when it opens: a retry after a failure sends the same one, so nothing is done twice.
  const key = useRef("");

  const fetchAll = useCallback(async (): Promise<Load> => {
    try {
      const [found, limits] = await Promise.all([api.actionsFor(clientId), api.limitsFor(clientId)]);
      const options = found.allowed.includes("withdrawal") ? await api.withdrawalOptions(clientId) : null;
      return { state: "ready", data: { found, limits, options } };
    } catch (failure) {
      return { state: "failed", message: actionErrorMessage(failure) };
    }
  }, [api, clientId]);

  useEffect(() => {
    let current = true;
    void fetchAll().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchAll]);

  async function run(command: () => Promise<void>, done: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await command();
      setForm(null);
      setNotice(done);
    } catch (failure) {
      setError(actionErrorMessage(failure));
      // Decided by someone else while it was on screen: show where it stands now.
      if (!(failure instanceof ActionError) || !["not_found", "waiting", "cannot_now"].includes(failure.code)) return;
    } finally {
      setBusy(false);
    }
    setLoad(await fetchAll());
  }

  const open = (next: Form) => {
    key.current = newKey();
    setForm(next);
    setError(null);
    setNotice(null);
  };
  const propose = (proposal: Proposal) => void run(() => api.propose(clientId, proposal, key.current), "Sent. Someone else must approve it before anything happens.");

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id={`${id}-title`} className="m-0 text-xl font-extrabold">
          Act for this client
        </h2>
        <p className="m-0 max-w-[720px] text-md leading-normal text-muted">
          One person proposes with a reason, and a different person approves. Nothing happens until the second person approves, and every step goes in the audit
          trail.
        </p>
      </div>
      {notice ? (
        <p role="status" className="m-0 max-w-[720px] rounded-md bg-success-surface px-3 py-2.5 text-sm font-semibold">
          {notice}
        </p>
      ) : null}
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-sm text-muted">
          Loading what is waiting for this client
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex max-w-[720px] flex-col items-start gap-2 rounded-xl border border-border bg-surface p-4">
          <p className="m-0 text-md text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchAll().then(setLoad);
            }}
          >
            Try again
          </Button>
        </div>
      ) : (
        <Ready
          data={load.data}
          form={form}
          busy={busy}
          error={error}
          canRefund={canRefund}
          onOpen={open}
          onPropose={propose}
          onDecide={(action, decision, reason) =>
            void run(
              () => api.decide(action.id, decision, reason, key.current),
              decision === "approved" ? KIND[action.kind].approved : decision === "rejected" ? "Rejected. Nothing changed." : "Withdrawn. Nothing changed.",
            )
          }
        />
      )}
    </section>
  );
}

function Ready({
  data,
  form,
  busy,
  error,
  canRefund,
  onOpen,
  onPropose,
  onDecide,
}: {
  data: Loaded;
  form: Form;
  busy: boolean;
  error: string | null;
  canRefund: boolean;
  onOpen: (form: Form) => void;
  onPropose: (proposal: Proposal) => void;
  onDecide: (action: ClientAction, decision: ActionDecision, reason: string) => void;
}) {
  const { found, limits, options } = data;
  const waiting = found.actions.filter((action) => action.status === "proposed");
  const decided = found.actions.filter((action) => action.status !== "proposed").slice(0, HISTORY);
  const startable = found.allowed.filter((kind) => !waiting.some((action) => action.kind === kind));
  const cancel = () => onOpen(null);
  const proposing = form && "kind" in form ? form.kind : null;

  return (
    <div className="flex max-w-[720px] flex-col gap-3">
      {waiting.map((action) => (
        <article
          key={action.id}
          aria-label={`${KIND[action.kind].title}, waiting`}
          className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="m-0 text-[17px] font-extrabold">{KIND[action.kind].title}</h3>
            <span className="rounded-pill bg-warning-surface px-2 py-0.5 text-2xs font-bold text-warning-text">Waiting for a second person</span>
          </div>
          <Details action={action} limits={limits} options={options} />
          <p className="m-0 text-md">“{action.reason}”</p>
          <p className="m-0 text-sm text-muted">
            Proposed by {action.yours ? "you" : action.proposedByName}, {formatTime(action.proposedAt)}.
          </p>
          <DecideArea action={action} form={form} busy={busy} error={error} allowed={found.allowed.includes(action.kind)} onOpen={onOpen} onDecide={onDecide} />
        </article>
      ))}

      {proposing === "pin_reset" ? (
        <FormCard title={KIND.pin_reset.title}>
          <p className="m-0 text-sm leading-normal">
            A forgotten PIN is put right by logging in again. Approving signs the client out of the phone app, so they log in with their number and code and
            choose a new PIN. Their sessions on the web stay. They are told by SMS, and by email if theirs is proved.
          </p>
          <ReasonForm
            label="Why?"
            hint="How you checked it is the client. The second person reads this, and it goes in the audit trail."
            submit="Send for approval"
            busy={busy}
            error={error}
            onSubmit={(reason) => onPropose({ kind: "pin_reset", reason })}
            onCancel={cancel}
          />
        </FormCard>
      ) : proposing === "withdrawal_limits" ? (
        <LimitsForm
          limits={limits}
          busy={busy}
          error={error}
          onSubmit={(next, reason) => onPropose({ kind: "withdrawal_limits", reason, limits: next })}
          onCancel={cancel}
        />
      ) : proposing === "statement" ? (
        <StatementForm
          busy={busy}
          error={error}
          onSubmit={(from, to, reason) => onPropose({ kind: "statement", reason, statement: { from, to } })}
          onCancel={cancel}
        />
      ) : proposing === "withdrawal" && options ? (
        <WithdrawalForm
          options={options}
          busy={busy}
          error={error}
          onSubmit={(choice, reason) => onPropose({ kind: "withdrawal", reason, withdrawal: choice })}
          onCancel={cancel}
        />
      ) : (
        <>
          {error && !(form && "decide" in form) ? <Alert>{error}</Alert> : null}
          {startable.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {startable.map((kind) => (
                <Button key={kind} variant="secondary" onClick={() => onOpen({ kind })}>
                  {KIND[kind].start}
                </Button>
              ))}
            </div>
          ) : found.allowed.length === 0 ? (
            <p className="m-0 text-sm text-muted">Your role cannot propose anything for a client. You can see what others proposed.</p>
          ) : null}
        </>
      )}

      <LimitsCard limits={limits} />

      {canRefund ? (
        <p className="m-0 text-sm leading-normal text-muted">
          To refund money the client paid that was never invested, or unwind an investment, propose a correction on{" "}
          <Link to="/investments" className="font-bold text-ink">
            Investments
          </Link>
          . A second person approves that too.
        </p>
      ) : null}

      <div className="flex flex-col gap-2">
        <h3 className="m-0 text-[17px] font-extrabold">Decided before</h3>
        {decided.length === 0 ? (
          <p className="m-0 text-sm text-muted">Nothing has been proposed for this client before.</p>
        ) : (
          <ul aria-label="Decided actions, newest first" className="m-0 flex list-none flex-col gap-2 p-0">
            {decided.map((action) => (
              <li key={action.id} className="flex flex-col gap-1 rounded-xl border border-border bg-surface p-3 text-sm">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <strong>{KIND[action.kind].title}</strong>
                  <span className="text-xs font-bold text-muted">{STATUS_WORDS[action.status]}</span>
                </div>
                <Details action={action} limits={limits} options={options} />
                <span className="text-muted">
                  Proposed by {action.yours ? "you" : action.proposedByName}
                  {action.decidedAt
                    ? `, ${action.status === "withdrawn" ? "withdrawn" : `${STATUS_WORDS[action.status].toLowerCase()} by ${action.decidedByName ?? "a colleague"}`} ${formatTime(action.decidedAt)}`
                    : ""}
                  .
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function FormCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div aria-label={title} role="group" className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <h3 className="m-0 text-[17px] font-extrabold">{title}</h3>
      {children}
    </div>
  );
}

/** What the action would do, as the second person needs to see it. */
function Details({ action, limits, options }: { action: ClientAction; limits: ClientLimits; options: WithdrawalOptions | null }) {
  if (action.kind === "statement" && action.statement) {
    return (
      <p className="m-0 text-sm">
        From {formatDay(action.statement.from)} to {formatDay(action.statement.to)}, to the client's app and proved email.
      </p>
    );
  }
  if (action.kind === "withdrawal_limits" && action.limits) {
    const set = LIMIT_KEYS.filter((key) => action.limits?.[key] !== null);
    if (set.length === 0) return <p className="m-0 text-sm">Clears the client's own limits, so the shared limits apply again.</p>;
    return (
      <dl className="m-0 grid gap-x-4 gap-y-1 text-sm [grid-template-columns:max-content_minmax(0,1fr)]">
        {set.map((key) => (
          <div key={key} className="contents">
            <dt className="font-bold text-muted">{LIMIT_LABELS[key]}</dt>
            <dd className="m-0 tabular-nums">
              {shillings(action.limits?.[key] ?? null)} <span className="text-muted">(shared {shillings(limits.shared[key])})</span>
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  if (action.kind === "withdrawal" && action.withdrawal) {
    const choice = action.withdrawal;
    const investment = options?.investments.find((item) => item.id === choice.investmentId);
    const account = options?.accounts.find((item) => item.id === choice.destinationId);
    const request = options?.requests.find((item) => item.id === choice.messageId);
    return (
      <div className="flex flex-col gap-1 text-sm">
        <p className="m-0">
          {investment
            ? `${investment.productName}, about ${formatAmount(investment.valueMinor, investment.currency)} today`
            : `Investment ${choice.investmentId}`}
          , paid to {account ? accountLabel(account) : `account ${choice.destinationId}`}.
        </p>
        {request ? (
          <blockquote className="m-0 border-l-2 border-border-strong pl-3 text-muted">
            The client wrote, {formatTime(request.createdAt)}: “{request.body}”
          </blockquote>
        ) : null}
      </div>
    );
  }
  return null;
}

function DecideArea({
  action,
  form,
  busy,
  error,
  allowed,
  onOpen,
  onDecide,
}: {
  action: ClientAction;
  form: Form;
  busy: boolean;
  error: string | null;
  allowed: boolean;
  onOpen: (form: Form) => void;
  onDecide: (action: ClientAction, decision: ActionDecision, reason: string) => void;
}) {
  const deciding = form && "decide" in form && form.decide.id === action.id ? form.decision : null;
  if (deciding) {
    const words = {
      approved: {
        label: "Note for the audit trail",
        hint: "What you checked before approving.",
        submit: KIND[action.kind].approve,
      },
      rejected: {
        label: "Why reject?",
        hint: "The person who proposed it reads this, and it goes in the audit trail.",
        submit: "Reject",
      },
      withdrawn: {
        label: "Why withdraw it?",
        hint: "This goes in the audit trail.",
        submit: "Withdraw",
      },
    }[deciding];
    return <ReasonForm {...words} busy={busy} error={error} onSubmit={(reason) => onDecide(action, deciding, reason)} onCancel={() => onOpen(null)} />;
  }
  if (action.yours) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <p className="m-0 text-xs text-muted">You proposed this, so someone else approves or rejects it.</p>
        <Button variant="secondary" onClick={() => onOpen({ decide: action, decision: "withdrawn" })}>
          Withdraw
        </Button>
      </div>
    );
  }
  if (!allowed) return <p className="m-0 text-xs text-muted">Someone whose role can do this approves or rejects it.</p>;
  return (
    <div className="flex flex-wrap gap-2">
      <Button onClick={() => onOpen({ decide: action, decision: "approved" })}>{KIND[action.kind].approve}</Button>
      <Button variant="secondary" onClick={() => onOpen({ decide: action, decision: "rejected" })}>
        Reject
      </Button>
    </div>
  );
}

function LimitsCard({ limits }: { limits: ClientLimits }) {
  return (
    <section aria-label="Withdrawal limits" className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-4">
      <h3 className="m-0 text-[17px] font-extrabold">Withdrawal limits</h3>
      <p className="m-0 text-sm text-muted">
        {limits.own ? "This client has their own stricter limits. A payout is checked against the lower one." : "The limits every client has."}
      </p>
      <dl className="m-0 grid gap-x-4 gap-y-1 text-sm [grid-template-columns:max-content_minmax(0,1fr)]">
        {LIMIT_KEYS.map((key) => {
          const tighter = limits.own?.[key] != null && limits.applied[key] !== limits.shared[key];
          return (
            <div key={key} className="contents">
              <dt className="font-bold text-muted">{LIMIT_LABELS[key]}</dt>
              <dd className="m-0 tabular-nums">
                {shillings(limits.applied[key])}
                {tighter ? <span className="text-muted"> (own; shared {shillings(limits.shared[key])})</span> : null}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

/** "1,500,000" or "1 500 000" as 1500000, empty as null, anything else as NaN. */
function parseShillings(text: string): number | null {
  const digits = text.replace(/[\s,]/g, "");
  if (!digits) return null;
  return /^\d{1,15}$/.test(digits) ? Number(digits) : Number.NaN;
}

function LimitsForm({
  limits,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  limits: ClientLimits;
  busy: boolean;
  error: string | null;
  onSubmit: (limits: Limits, reason: string) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [text, setText] = useState<Record<keyof Limits, string>>(() => {
    const start = {} as Record<keyof Limits, string>;
    for (const key of LIMIT_KEYS) start[key] = limits.own?.[key] != null ? String(limits.own[key]) : "";
    return start;
  });
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const values = Object.fromEntries(LIMIT_KEYS.map((key) => [key, parseShillings(text[key])])) as Record<keyof Limits, number | null>;
  const problem = (key: keyof Limits): string | null => {
    const value = values[key];
    const shared = limits.shared[key];
    if (value === null) return null;
    if (Number.isNaN(value) || value < 1) return "Write whole shillings, such as 500,000.";
    if (shared !== null && value >= shared) return `Must be lower than the shared ${shillings(shared)}.`;
    return null;
  };
  const nothing = LIMIT_KEYS.every((key) => values[key] === null) && !limits.own;

  function submit(event: FormEvent) {
    event.preventDefault();
    setTried(true);
    if (nothing || !reason.trim() || LIMIT_KEYS.some((key) => problem(key) !== null)) return;
    onSubmit(values, reason.trim());
  }

  return (
    <form aria-label={KIND.withdrawal_limits.title} onSubmit={submit} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <h3 className="m-0 text-[17px] font-extrabold">{KIND.withdrawal_limits.title}</h3>
      <p className="m-0 text-sm leading-normal">
        Stricter limits for this client alone, in whole shillings. They can only be lower than the limits every client has. Leave a box empty to keep the shared
        limit
        {limits.own ? ", or empty every box to clear the client's own limits" : ""}.
      </p>
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(220px,1fr))]">
        {LIMIT_KEYS.map((key) => {
          const wrong = tried ? problem(key) : null;
          return (
            <label key={key} htmlFor={`${id}-${key}`} className="flex flex-col gap-1.5 text-sm font-bold">
              {LIMIT_LABELS[key]}
              <span className="text-xs font-medium text-muted">Shared: {shillings(limits.shared[key])}</span>
              <input
                id={`${id}-${key}`}
                value={text[key]}
                inputMode="numeric"
                autoComplete="off"
                aria-invalid={wrong !== null}
                onChange={(event) => setText({ ...text, [key]: event.target.value })}
                className={INPUT}
              />
              {wrong ? <span className="text-xs font-bold text-danger">{wrong}</span> : null}
            </label>
          );
        })}
      </div>
      {tried && nothing ? <span className="text-xs font-bold text-danger">Set at least one limit.</span> : null}
      <ReasonField
        id={`${id}-reason`}
        value={reason}
        tried={tried}
        onChange={setReason}
        hint="Why this client needs stricter limits. The second person reads this."
      />
      {error ? <Alert>{error}</Alert> : null}
      <Actions busy={busy} onCancel={onCancel} />
    </form>
  );
}

function today(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function StatementForm({
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  busy: boolean;
  error: string | null;
  onSubmit: (from: string, to: string, reason: string) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState(today);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const wrong = tried && (!from || !to || from > to || to > today());

  function submit(event: FormEvent) {
    event.preventDefault();
    setTried(true);
    if (!from || !to || from > to || to > today() || !reason.trim()) return;
    onSubmit(from, to, reason.trim());
  }

  return (
    <form aria-label={KIND.statement.title} onSubmit={submit} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <h3 className="m-0 text-[17px] font-extrabold">{KIND.statement.title}</h3>
      <p className="m-0 text-sm leading-normal">
        The statement goes into the client's app and to their proved email, locked with their date of birth. It covers at most a year, ending today or earlier.
      </p>
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(200px,1fr))]">
        <label htmlFor={`${id}-from`} className="flex flex-col gap-1.5 text-sm font-bold">
          From
          <input
            id={`${id}-from`}
            type="date"
            value={from}
            max={today()}
            aria-invalid={wrong}
            onChange={(event) => setFrom(event.target.value)}
            className={INPUT}
          />
        </label>
        <label htmlFor={`${id}-to`} className="flex flex-col gap-1.5 text-sm font-bold">
          To
          <input id={`${id}-to`} type="date" value={to} max={today()} aria-invalid={wrong} onChange={(event) => setTo(event.target.value)} className={INPUT} />
        </label>
      </div>
      {wrong ? <span className="text-xs font-bold text-danger">Choose a start date, and an end date after it that is today or earlier.</span> : null}
      <ReasonField id={`${id}-reason`} value={reason} tried={tried} onChange={setReason} hint="Who asked for it and why. The second person reads this." />
      {error ? <Alert>{error}</Alert> : null}
      <Actions busy={busy} onCancel={onCancel} />
    </form>
  );
}

function WithdrawalForm({
  options,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  options: WithdrawalOptions;
  busy: boolean;
  error: string | null;
  onSubmit: (choice: { investmentId: string; destinationId: string; messageId: string }, reason: string) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [messageId, setMessageId] = useState("");
  const [investmentId, setInvestmentId] = useState("");
  const [destinationId, setDestinationId] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);

  const blocked = !options.open
    ? "Early withdrawal is switched off for every client, so staff cannot start one either."
    : options.requests.length === 0
      ? "The client has not written in their help chat in the last seven days. Staff start a withdrawal only when the client asks for it there in their own words, so ask them to write in the app's help chat."
      : options.investments.length === 0
        ? "None of the client's investments can be withdrawn early today."
        : options.accounts.length === 0
          ? "The client has no approved payout account that can be paid today. They add one in the app, and it is paid after its first day."
          : null;

  function submit(event: FormEvent) {
    event.preventDefault();
    setTried(true);
    if (!messageId || !investmentId || !destinationId || !reason.trim()) return;
    onSubmit({ investmentId, destinationId, messageId }, reason.trim());
  }

  if (blocked) {
    return (
      <FormCard title={KIND.withdrawal.title}>
        <p className="m-0 rounded-md bg-surface-sunken px-3 py-2.5 text-sm">{blocked}</p>
        <div>
          <Button variant="secondary" onClick={onCancel}>
            Close
          </Button>
        </div>
      </FormCard>
    );
  }

  return (
    <form aria-label={KIND.withdrawal.title} onSubmit={submit} className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-4">
      <h3 className="m-0 text-[17px] font-extrabold">{KIND.withdrawal.title}</h3>
      <p className="m-0 text-sm leading-normal">
        Only for a client who asked in their own help chat. It is paid only to an approved account of their own, the same as when they ask in the app, and they
        are told by SMS and email when it is approved.
      </p>
      <Choices
        legend="The client's request"
        hint="Their own message from the last seven days. One message backs one withdrawal."
        name={`${id}-message`}
        value={messageId}
        missing={tried && !messageId}
        onChange={setMessageId}
        items={options.requests.map((item) => ({
          id: item.id,
          label: `“${item.body}”`,
          detail: formatTime(item.createdAt),
        }))}
      />
      <Choices
        legend="Investment"
        hint="What they would be paid today, after tax."
        name={`${id}-investment`}
        value={investmentId}
        missing={tried && !investmentId}
        onChange={setInvestmentId}
        items={options.investments.map((item) => ({
          id: item.id,
          label: `${item.productName}, ${formatAmount(item.valueMinor, item.currency)}`,
          detail: item.maturityDate ? `Matures ${formatDay(item.maturityDate)}` : "",
        }))}
      />
      <Choices
        legend="Pay to"
        hint="Approved accounts of the client's own."
        name={`${id}-account`}
        value={destinationId}
        missing={tried && !destinationId}
        onChange={setDestinationId}
        items={options.accounts.map((item) => ({
          id: item.id,
          label: accountLabel(item),
          detail: item.kind === "bank" ? "Bank" : "Mobile money",
        }))}
      />
      <ReasonField
        id={`${id}-reason`}
        value={reason}
        tried={tried}
        onChange={setReason}
        hint="What you checked, such as calling the client back. The second person reads this."
      />
      {error ? <Alert>{error}</Alert> : null}
      <Actions busy={busy} onCancel={onCancel} />
    </form>
  );
}

function Choices({
  legend,
  hint,
  name,
  value,
  missing,
  onChange,
  items,
}: {
  legend: string;
  hint: string;
  name: string;
  value: string;
  missing: boolean;
  onChange: (value: string) => void;
  items: { id: string; label: string; detail: string }[];
}) {
  return (
    <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0">
      <legend className="p-0 text-sm font-bold">{legend}</legend>
      <span className="text-xs font-medium text-muted">{hint}</span>
      {items.map((item) => (
        <label
          key={item.id}
          className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-border-strong px-3 py-2.5 text-sm has-[:checked]:border-primary has-[:checked]:bg-surface-sunken"
        >
          <input type="radio" name={name} value={item.id} checked={value === item.id} onChange={() => onChange(item.id)} className="mt-0.5" />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="break-words font-medium">{item.label}</span>
            {item.detail ? <span className="text-xs text-muted">{item.detail}</span> : null}
          </span>
        </label>
      ))}
      {missing ? <span className="text-xs font-bold text-danger">Choose one.</span> : null}
    </fieldset>
  );
}

function ReasonField({ id, value, tried, hint, onChange }: { id: string; value: string; tried: boolean; hint: string; onChange: (value: string) => void }) {
  const missing = tried && !value.trim();
  return (
    <label htmlFor={id} className="flex flex-col gap-1.5 text-sm font-bold">
      Why?
      <span className="text-xs font-medium text-muted">{hint} It goes in the audit trail.</span>
      <textarea id={id} value={value} rows={2} maxLength={500} aria-invalid={missing} onChange={(event) => onChange(event.target.value)} className={INPUT} />
      {missing ? <span className="text-xs font-bold text-danger">Write a short reason for the audit trail.</span> : null}
    </label>
  );
}

function Actions({ busy, onCancel }: { busy: boolean; onCancel: () => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button type="submit" disabled={busy}>
        {busy ? "Sending" : "Send for approval"}
      </Button>
      <Button type="button" variant="secondary" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}
