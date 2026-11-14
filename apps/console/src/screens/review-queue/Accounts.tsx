import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import type { ClientCard, Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import {
  ReviewQueueError,
  reviewQueueErrorMessage,
  type NewCheck,
  type PayoutAccount,
  type PayoutAccountDetail,
  type RejectionReason,
  type ReviewQueueApi,
} from "./api.ts";
import {
  accountLine,
  formatDay,
  formatMoment,
  KIND_LABELS,
  PROOF_LABELS,
  providerName,
  REJECTION_LABELS,
  timeAgo,
} from "./labels.ts";
import { useNames } from "./names.ts";
import { Empty, ErrorNote, Failure, FIELD, FieldNote, Loading, Notice, Panel, Pill, QueueButton, Row, Section, SELECT, type Load, type Tone } from "./Parts.tsx";

const MAX_REASON = 500;
const MAX_NAME = 120;

/** Refusals after which what is on screen is out of date, so the account and the list are read again. */
const RELOAD_AFTER = new Set(["conflict", "destination_not_checked", "not_found"]);

interface AccountsProps {
  api: ReviewQueueApi;
  directory: Directory;
  /** The signed-in member of staff, to tell their own checks apart. */
  me: string;
  /** Whether this person holds `payout_accounts.change`. Without it they only look. */
  canDecide: boolean;
  now: () => Date;
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** Tells the page how many accounts wait, for the tab. */
  onCount?: (count: number) => void;
}

type Stage = "needs_check" | "needs_second" | "checked_by_me" | "approved" | "rejected" | "removed" | "other";

function stageOf(account: PayoutAccount, me: string): Stage {
  if (account.status === "in_review") {
    if (!account.check) return "needs_check";
    return account.check.checkedBy === me ? "checked_by_me" : "needs_second";
  }
  if (account.status === "approved" || account.status === "rejected" || account.status === "removed") return account.status;
  return "other";
}

const STAGE_LABELS: Record<Stage, { text: string; tone: Tone }> = {
  needs_check: { text: "Needs a check", tone: "waiting" },
  needs_second: { text: "Needs a second person", tone: "waiting" },
  checked_by_me: { text: "Checked by you", tone: "quiet" },
  approved: { text: "Approved", tone: "done" },
  rejected: { text: "Proof refused", tone: "refused" },
  removed: { text: "Removed", tone: "refused" },
  other: { text: "Not in review", tone: "quiet" },
};

export function AccountsSection({ api, directory, me, canDecide, now, selectedId, onSelect, onCount }: AccountsProps) {
  const [list, setList] = useState<Load<PayoutAccount[]>>({ state: "loading" });
  // Kept with the account it belongs to, so choosing another account shows neither.
  const [opened, setOpened] = useState<{ id: string; load: Load<PayoutAccountDetail> } | null>(null);
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const fetchList = useCallback(
    (): Promise<Load<PayoutAccount[]>> =>
      api.listAccounts().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: reviewQueueErrorMessage(error) }),
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

  useEffect(() => {
    if (list.state === "ready") onCount?.(list.value.length);
  }, [list, onCount]);

  // Opening an account shows its whole number and is written to the audit trail, so it is
  // opened only when someone chooses it, never just because the queue loaded.
  useEffect(() => {
    if (!selectedId) return;
    let current = true;
    const show = (load: Load<PayoutAccountDetail>) => current && setOpened({ id: selectedId, load });
    api.openAccount(selectedId).then(
      (value) => show({ state: "ready", value }),
      (error: unknown) => show({ state: "failed", message: reviewQueueErrorMessage(error) }),
    );
    return () => {
      current = false;
    };
  }, [api, selectedId, attempt]);

  const items = list.state === "ready" ? list.value : [];
  const { clients, staff } = useNames(
    directory,
    [...items.map((item) => item.clientId), ...(opened?.load.state === "ready" ? [opened.load.value.clientId] : [])],
    [
      ...items.flatMap((item) => (item.check ? [item.check.checkedBy] : [])),
      ...(opened?.load.state === "ready" && opened.load.value.check ? [opened.load.value.check.checkedBy] : []),
    ],
  );
  const who = (id: string) => (id === me ? "you" : (staff[id] ?? "a colleague"));

  const account: Load<PayoutAccountDetail> = opened?.id === selectedId ? opened.load : { state: "loading" };
  const decisionError = failed?.id === selectedId ? failed.message : null;

  async function decide(input: DecisionInput) {
    if (!selectedId || account.state !== "ready") return;
    const id = selectedId;
    setBusy(true);
    setFailed(null);
    try {
      const updated =
        input.action === "check"
          ? await api.check(id, input.check)
          : input.action === "approve"
            ? await api.approve(id, input.reason)
            : input.action === "reject"
              ? await api.reject(id, input.rejectionReason, input.reason)
              : await api.remove(id, input.reason);
      // The answer has the masked number only. What was opened is kept with the new state.
      setOpened({ id, load: { state: "ready", value: { ...account.value, ...updated } } });
      await reloadList();
    } catch (error) {
      setFailed({ id, message: reviewQueueErrorMessage(error) });
      if (error instanceof ReviewQueueError && RELOAD_AFTER.has(error.code)) {
        setAttempt((count) => count + 1);
        await reloadList();
      }
    } finally {
      setBusy(false);
    }
  }

  if (list.state === "loading") return <Loading>Loading payout accounts</Loading>;
  if (list.state === "failed") {
    return <Failure title="Payout accounts could not be loaded" message={list.message} onRetry={() => void reloadList()} />;
  }
  if (items.length === 0 && !selectedId) {
    return (
      <Empty title="No accounts to check">
        Every payout account has been decided. New ones appear here when a client sends proof that an account is theirs.
      </Empty>
    );
  }

  return (
    <div className="flex flex-wrap items-start gap-5">
      <div className="min-w-0 flex-[1_1_260px]">
        {items.length === 0 ? (
          <p className="m-0 text-md text-muted">No other accounts are waiting.</p>
        ) : (
          <ul aria-label="Payout accounts" className="m-0 flex list-none flex-col gap-2.5 p-0">
            {items.map((item) => {
              const stage = STAGE_LABELS[stageOf(item, me)];
              return (
                <li key={item.id}>
                  <QueueButton selected={item.id === selectedId} onSelect={() => onSelect(item.id)}>
                    <span className="text-md font-bold">{clientName(clients[item.clientId])}</span>
                    <span className="text-xs text-muted">
                      {accountLine(item)}, added {timeAgo(item.createdAt, now())}
                    </span>
                    <span className={cn("mt-1 text-xs font-bold", stage.tone === "waiting" ? "text-warning-text" : "text-muted")}>
                      {item.check && item.check.checkedBy !== me ? `Checked by ${who(item.check.checkedBy)}, needs a second person` : stage.text}
                    </span>
                  </QueueButton>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div className="min-w-0 flex-[3_1_480px]">
        {!selectedId ? (
          <Notice>
            Choose an account to check it. Opening one shows its whole number and the proof, and is recorded in the audit
            trail.
          </Notice>
        ) : account.state === "loading" ? (
          <Loading>Opening this account</Loading>
        ) : account.state === "failed" ? (
          <Failure
            title="This account could not be opened"
            message={account.message}
            onRetry={() => {
              setOpened(null);
              setAttempt((count) => count + 1);
            }}
          />
        ) : (
          <AccountReview
            key={`${account.value.id}:${account.value.status}:${account.value.check?.checkedAt ?? ""}`}
            account={account.value}
            client={clients[account.value.clientId]}
            who={who}
            me={me}
            busy={busy}
            error={decisionError}
            canDecide={canDecide}
            onDecide={(input) => void decide(input)}
          />
        )}
      </div>
    </div>
  );
}

function clientName(card: ClientCard | null | undefined): string {
  return card?.name ?? "A client";
}

type Action = "check" | "approve" | "reject" | "remove";

export type DecisionInput =
  | { action: "check"; check: NewCheck }
  | { action: "approve"; reason: string }
  | { action: "reject"; rejectionReason: RejectionReason; reason: string }
  | { action: "remove"; reason: string };

interface ReviewProps {
  account: PayoutAccountDetail;
  client: ClientCard | null | undefined;
  who: (staffId: string) => string;
  me: string;
  busy: boolean;
  error: string | null;
  canDecide: boolean;
  onDecide: (input: DecisionInput) => void;
}

function AccountReview({ account, client, who, me, busy, error, canDecide, onDecide }: ReviewProps) {
  const stage = stageOf(account, me);
  const pill = STAGE_LABELS[stage];
  const name = clientName(client);
  return (
    <Panel label={`Payout account for ${name}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <h2 className="m-0 text-2xl font-extrabold">{name}</h2>
          {client?.accountNumber ? <span className="text-sm text-muted tabular-nums">Finvest account {client.accountNumber}</span> : null}
        </div>
        <Pill tone={pill.tone}>{pill.text}</Pill>
      </div>

      <div className="flex flex-wrap gap-5">
        <Section title="The account">
          <Row name="Kind" value={KIND_LABELS[account.kind]} />
          <Row name={account.kind === "bank" ? "Bank" : "Network"} value={providerName(account)} />
          <Row name="Number" value={<span className="tabular-nums">{account.accountNumber}</span>} />
          <Row name="Name the client gave" value={account.holderName} />
          <Row name="Added" value={formatMoment(account.createdAt)} />
        </Section>
        <Section title="The proof">
          <Row name="Proof" value={account.proofKind ? PROOF_LABELS[account.proofKind] : "None yet"} />
          <Row
            name="File"
            value={
              account.proofLink ? (
                <a href={account.proofLink.url} target="_blank" rel="noreferrer" className="text-ink underline">
                  Open the proof
                </a>
              ) : account.proofKind === "network_registered_name" ? (
                "Read through the provider"
              ) : (
                "Cannot be shown"
              )
            }
          />
          <Row name="Name on the client's ID" value={account.idName ?? "No approved identity check"} />
        </Section>
      </div>

      {account.check ? (
        <Section title={account.status === "in_review" ? "The check, waiting for a second person" : "The check"}>
          <Row name="Name on the proof" value={account.check.proofName} />
          <Row name="Against the ID" value={account.check.nameMatch === "exact" ? "The same names" : "Partly the same"} />
          {account.check.statementDate ? <Row name="Statement date" value={formatDay(account.check.statementDate)} /> : null}
          <Row name="Checked by" value={`${capitalise(who(account.check.checkedBy))}, ${formatMoment(account.check.checkedAt)}`} />
        </Section>
      ) : null}

      {account.check?.nameMatch === "partial" && account.status === "in_review" ? (
        <p className="m-0 rounded-md bg-warning-surface px-3.5 py-3 text-md leading-normal text-warning-text">
          One name has a name the other lacks. Approve only if you judge it is the same person.
        </p>
      ) : null}

      <Outcome account={account} />

      {account.status === "in_review" && canDecide ? (
        <DecisionForm account={account} stage={stage} busy={busy} error={error} onDecide={onDecide} />
      ) : account.status === "in_review" ? (
        <p className="m-0 border-t border-divider pt-4 text-md leading-normal text-muted">
          You can look at this account. Deciding it needs the payout accounts permission, held by Admin, Operations and
          Compliance.
        </p>
      ) : error ? (
        <ErrorNote>{error}</ErrorNote>
      ) : null}
    </Panel>
  );
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** What has already happened to an account that is no longer in review. */
function Outcome({ account }: { account: PayoutAccountDetail }) {
  let text: ReactNode = null;
  if (account.status === "approved") {
    text = account.payableFrom ? (
      <>
        Approved. It can be paid from <strong>{formatMoment(account.payableFrom)}</strong>, 24 hours after the approval.
      </>
    ) : (
      "Approved."
    );
  } else if (account.status === "rejected") {
    const reason = account.rejectionReason ? REJECTION_LABELS[account.rejectionReason] : null;
    text =
      account.rejectionReason === "suspected_fraud"
        ? "Proof refused for suspected fraud. This is final: the account can only be removed."
        : `Proof refused${reason ? `: ${reason.toLowerCase()}` : ""}. The client can send better proof.`;
  } else if (account.status === "removed") {
    text = "Removed for good. This account will never be paid again.";
  } else if (account.status !== "in_review") {
    text = "This account is not waiting for staff.";
  }
  return text ? <Notice>{text}</Notice> : null;
}

const ACTION_LABELS: Record<Action, string> = {
  check: "Record the check",
  approve: "Approve the check",
  reject: "Refuse the proof",
  remove: "Remove the account",
};

/** Approving and removing cannot be undone, and suspected fraud is final, so each asks once more. */
function confirmation(action: Action, rejectionReason: RejectionReason | ""): { text: string; confirm: string } | null {
  if (action === "approve") {
    return { text: "Approving lets Finvest pay this account 24 hours from now. It cannot be undone.", confirm: "Yes, approve" };
  }
  if (action === "remove") return { text: "Removing is final. This account will never be paid again.", confirm: "Yes, remove" };
  if (action === "reject" && rejectionReason === "suspected_fraud") {
    return { text: "Suspected fraud is final. The client cannot prove this account again.", confirm: "Yes, refuse" };
  }
  return null;
}

type FieldErrors = Partial<Record<"proofName" | "statementDate" | "rejectionReason" | "reason", string>>;

function DecisionForm({
  account,
  stage,
  busy,
  error,
  onDecide,
}: {
  account: PayoutAccountDetail;
  stage: Stage;
  busy: boolean;
  error: string | null;
  onDecide: (input: DecisionInput) => void;
}) {
  const ids = useId();
  const actions: Action[] =
    stage === "needs_check" ? ["check", "reject", "remove"] : stage === "needs_second" ? ["approve", "check", "reject", "remove"] : ["check", "reject", "remove"];
  const [action, setAction] = useState<Action>(stage === "checked_by_me" ? "reject" : actions[0]!);
  const [proofName, setProofName] = useState("");
  const [statementDate, setStatementDate] = useState("");
  const [rejectionReason, setRejectionReason] = useState<RejectionReason | "">("");
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [confirming, setConfirming] = useState(false);
  const confirmButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) confirmButton.current?.focus();
  }, [confirming]);

  const needsDate = account.proofKind === "bank_statement";

  function problems(): FieldErrors {
    const found: FieldErrors = {};
    if (action === "check") {
      if (!proofName.trim()) found.proofName = "Type the name exactly as the proof shows it.";
      if (needsDate && !statementDate) found.statementDate = "Give the date printed on the statement.";
    }
    if (action === "reject" && !rejectionReason) found.rejectionReason = "Choose why the proof is refused.";
    if (!reason.trim()) found.reason = "Give the reason for your decision.";
    return found;
  }

  function attempt(confirmed = false) {
    const found = problems();
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setConfirming(false);
      return;
    }
    if (!confirmed && confirmation(action, rejectionReason)) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    const why = reason.trim();
    if (action === "check") {
      onDecide({ action, check: { proofName: proofName.trim(), statementDate: needsDate ? statementDate : null, reason: why } });
    } else if (action === "reject") {
      onDecide({ action, rejectionReason: rejectionReason as RejectionReason, reason: why });
    } else {
      onDecide({ action, reason: why });
    }
  }

  const asked = confirming ? confirmation(action, rejectionReason) : null;

  return (
    <form aria-label="Decision" className="flex flex-col gap-4 border-t border-divider pt-4" onSubmit={(event: FormEvent) => event.preventDefault()}>
      {stage === "checked_by_me" ? (
        <Notice>You made this check, so a different person must approve it. You can still check again, refuse the proof or remove the account.</Notice>
      ) : null}

      <div role="tablist" aria-label="What to do" className="flex flex-wrap gap-2">
        {actions.map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={action === item}
            onClick={() => {
              setAction(item);
              setErrors({});
              setConfirming(false);
            }}
            className={cn(
              "h-11 cursor-pointer rounded-pill px-4 text-sm",
              action === item ? "bg-black font-bold text-white" : "border border-border-strong bg-surface font-semibold text-ink",
            )}
          >
            {item === "check" && account.check ? "Check again" : ACTION_LABELS[item]}
          </button>
        ))}
      </div>

      {action === "check" ? (
        <>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${ids}-name`}>Name on the proof</Label>
            <Input
              id={`${ids}-name`}
              value={proofName}
              maxLength={MAX_NAME}
              onChange={(event) => setProofName(event.target.value)}
              aria-invalid={errors.proofName ? true : undefined}
              aria-describedby={`${ids}-name-help`}
              autoComplete="off"
              className={FIELD}
            />
            <FieldNote id={`${ids}-name-help`} error={errors.proofName}>
              Type it as the proof shows it. Finvest compares it with the ID, and a different name is never approved.
            </FieldNote>
          </div>
          {needsDate ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${ids}-date`}>Date on the statement</Label>
              <Input
                id={`${ids}-date`}
                type="date"
                value={statementDate}
                onChange={(event) => setStatementDate(event.target.value)}
                aria-invalid={errors.statementDate ? true : undefined}
                aria-describedby={`${ids}-date-help`}
                className={cn(FIELD, "max-w-[240px]")}
              />
              <FieldNote id={`${ids}-date-help`} error={errors.statementDate}>
                It must be within the last three calendar months.
              </FieldNote>
            </div>
          ) : null}
        </>
      ) : null}

      {action === "approve" && account.check ? (
        <p className="m-0 text-md leading-normal">
          You are approving the check as it was recorded: <strong>{account.check.proofName}</strong> on the proof,{" "}
          {account.check.nameMatch === "exact" ? "the same names as the ID" : "partly the same as the ID"}.
        </p>
      ) : null}

      {action === "reject" ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${ids}-why`}>Why the proof is refused</Label>
          <select
            id={`${ids}-why`}
            value={rejectionReason}
            onChange={(event) => setRejectionReason(event.target.value as RejectionReason | "")}
            aria-invalid={errors.rejectionReason ? true : undefined}
            aria-describedby={`${ids}-why-help`}
            className={SELECT}
          >
            <option value="">Choose a reason</option>
            {(Object.keys(REJECTION_LABELS) as RejectionReason[]).map((key) => (
              <option key={key} value={key}>
                {REJECTION_LABELS[key]}
              </option>
            ))}
          </select>
          <FieldNote id={`${ids}-why-help`} error={errors.rejectionReason}>
            The client can send better proof, unless it is suspected fraud.
          </FieldNote>
        </div>
      ) : null}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-reason`}>Reason for your decision</Label>
        <Input
          id={`${ids}-reason`}
          value={reason}
          maxLength={MAX_REASON}
          onChange={(event) => setReason(event.target.value)}
          aria-invalid={errors.reason ? true : undefined}
          aria-describedby={`${ids}-reason-help`}
          className={FIELD}
        />
        <FieldNote id={`${ids}-reason-help`} error={errors.reason}>
          {action === "remove" ? "Recorded as an operator control. The client does not see it." : "Kept in the audit trail. The client does not see it."}
        </FieldNote>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {asked ? (
        <div role="alertdialog" aria-label="Confirm" className="flex flex-col gap-3 rounded-md bg-warning-surface p-3.5">
          <p className="m-0 text-md font-semibold text-warning-text">{asked.text}</p>
          <div className="flex flex-wrap gap-2.5">
            <Button ref={confirmButton} disabled={busy} onClick={() => attempt(true)}>
              {busy ? "Saving" : asked.confirm}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setConfirming(false)}>
              Go back
            </Button>
          </div>
        </div>
      ) : (
        <Button variant={action === "approve" || action === "check" ? "primary" : "secondary"} disabled={busy} onClick={() => attempt()} className="h-12 self-start px-6">
          {busy ? "Saving" : action === "check" && account.check ? "Check again" : ACTION_LABELS[action]}
        </Button>
      )}
    </form>
  );
}
