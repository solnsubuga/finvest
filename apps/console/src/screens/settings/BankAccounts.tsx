import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import type { Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import {
  BANK_ACCOUNT_LIMITS,
  BankAccountsError,
  bankAccountProblemText,
  bankAccountsErrorMessage,
  checkBankAccount,
  CURRENCY_NAMES,
  type BankAccountDecision,
  type BankAccountDetails,
  type BankAccountField,
  type BankAccountProblem,
  type BankAccountsApi,
  type Currency,
  type CurrencyAccount,
  type NewBankAccount,
} from "./bankAccounts.ts";
import { stamp } from "./labels.ts";

const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3.5 py-3 text-md font-medium text-ink aria-invalid:border-danger";
const DETAILS: readonly { key: keyof BankAccountDetails; label: string }[] = [
  { key: "bankName", label: "Bank" },
  { key: "accountName", label: "Account name" },
  { key: "accountNumber", label: "Account number" },
  { key: "branch", label: "Branch" },
];
const FIELDS: readonly { field: BankAccountField; label: string; hint: string | null; inputMode?: "numeric" }[] = [
  { field: "bankName", label: "Bank", hint: null },
  { field: "accountName", label: "Account name", hint: "Exactly as the bank holds it. Clients type it into their bank's app." },
  { field: "accountNumber", label: "Account number", hint: "Digits only. Spaces and dashes are taken out.", inputMode: "numeric" },
  { field: "branch", label: "Branch (optional)", hint: null },
];

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; accounts: CurrencyAccount[] };
type Failure = { on: string; message: string; problems: readonly BankAccountProblem[] };

interface BankAccountsSectionProps {
  api: BankAccountsApi;
  directory: Directory;
  /** `bank_accounts.change`: propose and decide. The API checks it again. */
  canChange: boolean;
}

/** The bank account clients send transfers to, one per currency, changed by two people. */
export function BankAccountsSection({ api, directory, canChange }: BankAccountsSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [editing, setEditing] = useState<Currency | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<Failure | null>(null);
  const [names, setNames] = useState<Record<string, string | null>>({});
  const asked = useRef(new Set<string>());

  const fetchList = useCallback(
    (): Promise<Load> =>
      api.list().then(
        (accounts) => {
          for (const staffId of accounts.flatMap((item) => (item.proposal ? [item.proposal.proposedBy] : []))) {
            if (asked.current.has(staffId)) continue;
            asked.current.add(staffId);
            void directory.staffName(staffId).then(
              (name) => setNames((known) => ({ ...known, [staffId]: name })),
              () => setNames((known) => ({ ...known, [staffId]: null })),
            );
          }
          return { state: "ready", accounts };
        },
        (error: unknown) => ({ state: "failed", message: bankAccountsErrorMessage(error) }),
      ),
    [api, directory],
  );

  useEffect(() => {
    let current = true;
    void fetchList().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchList]);

  async function run(on: string, command: () => Promise<void>): Promise<boolean> {
    setBusy(true);
    setFailed(null);
    try {
      await command();
      return true;
    } catch (error) {
      setFailed({ on, message: bankAccountsErrorMessage(error), problems: error instanceof BankAccountsError ? error.problems : [] });
      return false;
    } finally {
      // The cards always show what Finvest now holds, after a change or a refusal.
      const fresh = await fetchList();
      if (fresh.state === "ready") setLoad(fresh);
      setBusy(false);
    }
  }

  const staffName = (staffId: string) => names[staffId] ?? "a member of staff";

  return (
    <section aria-labelledby="bank-accounts" className="flex flex-col gap-4">
      <div className="flex max-w-[720px] flex-col gap-1">
        <h2 id="bank-accounts" className="m-0 text-xl font-extrabold">
          Bank account for transfers
        </h2>
        <p className="m-0 text-md leading-normal text-muted">
          Where clients are told to send money when they pay by bank transfer. It must be the trust account whose statements
          reconciliation imports, or no transfer can be matched. One person proposes a change and someone else approves it.
        </p>
      </div>

      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading the bank account
        </p>
      ) : load.state === "failed" ? (
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">The bank account could not be loaded</h3>
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
        <div className="flex flex-wrap items-start gap-4">
          {load.accounts.map((item) => (
            <CurrencyCard
              key={`${item.currency}:${item.proposal?.id ?? "none"}:${item.changedAt ?? "never"}`}
              item={item}
              canChange={canChange}
              editing={editing === item.currency}
              staffName={staffName}
              busy={busy}
              failure={failed?.on === item.currency ? failed : null}
              onEdit={(open) => {
                setEditing(open ? item.currency : null);
                setFailed(null);
              }}
              onPropose={(input) =>
                run(item.currency, () => api.propose(item.currency, input)).then((done) => {
                  if (done) setEditing(null);
                  return done;
                })
              }
              onDecide={(decision, reason) =>
                item.proposal ? run(item.currency, () => api.decide(item.proposal?.id ?? "", decision, reason)) : Promise.resolve(false)
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}

interface CurrencyCardProps {
  item: CurrencyAccount;
  canChange: boolean;
  editing: boolean;
  staffName: (staffId: string) => string;
  busy: boolean;
  failure: Failure | null;
  onEdit: (open: boolean) => void;
  onPropose: (input: NewBankAccount) => Promise<boolean>;
  onDecide: (decision: BankAccountDecision, reason: string) => Promise<boolean>;
}

function CurrencyCard({ item, canChange, editing, staffName, busy, failure, onEdit, onPropose, onDecide }: CurrencyCardProps) {
  const { currency, inUse, proposal } = item;
  const title = `${CURRENCY_NAMES[currency]} (${currency})`;
  return (
    <article aria-label={title} className="flex min-w-0 flex-[1_1_340px] flex-col gap-3 rounded-[20px] border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="m-0 text-[17px] font-extrabold">{title}</h3>
        {proposal ? (
          <span className="rounded-pill bg-warning-surface px-2.5 py-1 text-xs font-bold text-warning-text">Change waiting</span>
        ) : inUse ? (
          <span className="rounded-pill bg-success-surface px-2.5 py-1 text-xs font-bold text-success">In use</span>
        ) : (
          <span className="rounded-pill bg-surface-sunken px-2.5 py-1 text-xs font-bold text-muted">Not set</span>
        )}
      </div>

      {inUse ? (
        <>
          <Details account={inUse} />
          {item.changedAt ? <p className="m-0 text-xs text-muted">Approved {stamp(item.changedAt)}</p> : null}
        </>
      ) : (
        <p className="m-0 text-md leading-normal text-muted">
          No account is set, so clients cannot pay in {currency} by bank transfer. The app offers mobile money instead.
        </p>
      )}

      {proposal ? (
        <ProposalBlock
          key={proposal.id}
          currency={currency}
          inUse={inUse}
          proposal={proposal}
          canChange={canChange}
          staffName={staffName}
          busy={busy}
          error={failure?.message ?? null}
          onDecide={onDecide}
        />
      ) : editing ? (
        <AccountForm
          currency={currency}
          inUse={inUse}
          busy={busy}
          error={failure?.message ?? null}
          refused={failure?.problems ?? []}
          onCancel={() => onEdit(false)}
          onSubmit={onPropose}
        />
      ) : (
        <>
          {failure ? <Refusal message={failure.message} /> : null}
          {canChange ? (
            <div>
              <Button variant={inUse ? "secondary" : "primary"} onClick={() => onEdit(true)}>
                {inUse ? "Change the account" : "Set the account"}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </article>
  );
}

function Details({ account, compare }: { account: BankAccountDetails; compare?: BankAccountDetails | null }) {
  return (
    <dl className="m-0 grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-1.5 text-md">
      {DETAILS.map(({ key, label }) => {
        const value = account[key];
        const changed = compare !== undefined && compare !== null && compare[key] !== value;
        return (
          <div key={key} className="contents">
            <dt className="text-sm font-semibold text-muted">{label}</dt>
            <dd className={cn("m-0 min-w-0 font-semibold break-words", key === "accountNumber" && "tabular-nums tracking-[0.4px]")}>
              {value ?? <span className="font-medium text-muted">None given</span>}
              {changed ? <span className="ml-2 rounded-pill bg-warning-surface px-2 py-0.5 text-xs font-bold text-warning-text">Changed</span> : null}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function Refusal({ message }: { message: string }) {
  return (
    <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
      {message}
    </p>
  );
}

interface ProposalBlockProps {
  currency: Currency;
  inUse: BankAccountDetails | null;
  proposal: NonNullable<CurrencyAccount["proposal"]>;
  canChange: boolean;
  staffName: (staffId: string) => string;
  busy: boolean;
  error: string | null;
  onDecide: (decision: BankAccountDecision, reason: string) => Promise<boolean>;
}

function ProposalBlock({ currency, inUse, proposal, canChange, staffName, busy, error, onDecide }: ProposalBlockProps) {
  const [deciding, setDeciding] = useState<BankAccountDecision | null>(null);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const missing = tried && note.trim().length === 0;

  return (
    <section aria-label="Change waiting for a second person" className="flex flex-col gap-3 rounded-[16px] border border-warning-surface bg-paper p-4">
      <h4 className="m-0 text-md font-extrabold">{inUse ? "Proposed new details" : "Proposed account"}</h4>
      <Details account={proposal.account} compare={inUse} />
      <p className="m-0 text-sm leading-normal text-muted">
        Proposed by {proposal.yours ? "you" : staffName(proposal.proposedBy)}, {stamp(proposal.proposedAt)}. {proposal.reason}
      </p>
      {error ? <Refusal message={error} /> : null}
      {!canChange ? <p className="m-0 text-sm text-muted">Someone who can change the bank account approves it.</p> : null}
      {canChange && !deciding ? (
        proposal.yours ? (
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-muted">Someone else must approve or reject your change.</span>
            <Button variant="secondary" disabled={busy} onClick={() => setDeciding("withdrawn")}>
              Withdraw it
            </Button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="m-0 text-sm leading-normal text-muted">
              Check every detail against the bank's own letter or statement before you approve. From then on, every client paying in{" "}
              {currency} by transfer is told to send money here.
            </p>
            <div className="flex gap-2">
              <Button disabled={busy} onClick={() => setDeciding("approved")}>
                Approve
              </Button>
              <Button variant="secondary" disabled={busy} onClick={() => setDeciding("rejected")}>
                Reject
              </Button>
            </div>
          </div>
        )
      ) : null}
      {deciding ? (
        <form
          aria-label="Decide the change"
          className="flex flex-col gap-2 border-t border-divider pt-3"
          onSubmit={(event) => {
            event.preventDefault();
            setTried(true);
            if (note.trim().length === 0) return;
            void onDecide(deciding, note);
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm font-bold">
            {deciding === "approved" ? "What did you check it against?" : deciding === "rejected" ? "What is wrong with it?" : "Why withdraw it?"}
            <textarea value={note} rows={2} aria-invalid={missing} onChange={(event) => setNote(event.target.value)} className={INPUT} />
          </label>
          {missing ? <p className="m-0 text-xs font-bold text-danger">Write a short reason for the audit trail.</p> : null}
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Saving" : deciding === "approved" ? "Approve the account" : deciding === "rejected" ? "Reject the change" : "Withdraw the change"}
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setDeciding(null);
                setTried(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : null}
    </section>
  );
}

interface AccountFormProps {
  currency: Currency;
  inUse: BankAccountDetails | null;
  busy: boolean;
  error: string | null;
  refused: readonly BankAccountProblem[];
  onCancel: () => void;
  onSubmit: (input: NewBankAccount) => Promise<boolean>;
}

function AccountForm({ currency, inUse, busy, error, refused, onCancel, onSubmit }: AccountFormProps) {
  const [draft, setDraft] = useState<NewBankAccount>(() => ({
    bankName: inUse?.bankName ?? "",
    accountName: inUse?.accountName ?? "",
    accountNumber: inUse?.accountNumber ?? "",
    branch: inUse?.branch ?? "",
    reason: "",
  }));
  const [tried, setTried] = useState(false);
  const live = checkBankAccount(draft);
  // Missing details are shown once the person tries to send. Broken rules are shown as they type.
  const problems = [...live.filter((problem) => tried || problem.code !== "required"), ...(tried ? refused : [])];
  const codesFor = (field: BankAccountField) => [...new Set(problems.filter((p) => p.field === field).map((p) => p.code))];
  const field = (name: BankAccountField, label: string, hint: string | null, input: ReactNode) => {
    const codes = codesFor(name);
    return (
      <label key={name} className="flex flex-col gap-1.5 text-sm font-bold">
        {label}
        {hint ? <span className="text-xs font-medium text-muted">{hint}</span> : null}
        {input}
        {codes.map((code) => (
          <span key={code} className="text-xs font-bold text-danger">
            {bankAccountProblemText(code, name)}
          </span>
        ))}
      </label>
    );
  };

  return (
    <form
      aria-label={`New ${currency} account details`}
      className="flex flex-col gap-3 border-t border-divider pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (live.length > 0) return;
        void onSubmit(draft);
      }}
    >
      <h4 className="m-0 text-md font-extrabold">{inUse ? "New details" : "The account"}</h4>
      {FIELDS.map(({ field: name, label, hint, inputMode }) =>
        field(
          name,
          label,
          hint,
          <input
            value={draft[name]}
            inputMode={inputMode}
            autoComplete="off"
            maxLength={BANK_ACCOUNT_LIMITS[name] + 20}
            aria-invalid={codesFor(name).length > 0}
            onChange={(event) => setDraft({ ...draft, [name]: event.target.value })}
            className={INPUT}
          />,
        ),
      )}
      {field(
        "reason",
        "Why is it changing?",
        "Staff only, for the person who approves it and the audit trail. Say where the details come from.",
        <textarea
          value={draft.reason}
          rows={2}
          aria-invalid={codesFor("reason").length > 0}
          onChange={(event) => setDraft({ ...draft, reason: event.target.value })}
          className={INPUT}
        />,
      )}
      {error ? <Refusal message={error} /> : null}
      <p className="m-0 text-xs leading-normal text-muted">
        {inUse ? "Clients keep being shown the account in use until someone else approves the new details." : "Clients are shown nothing until someone else approves it."}
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
