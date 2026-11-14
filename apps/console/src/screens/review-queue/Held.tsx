import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import type { ClientCard, Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import {
  ReviewQueueError,
  reviewQueueErrorMessage,
  type DecidedPayout,
  type HeldAction,
  type HeldPayout,
  type ReviewQueueApi,
} from "./api.ts";
import { formatAmount, formatDay, formatMoment, KIND_LABELS, timeAgo } from "./labels.ts";
import { useNames } from "./names.ts";
import { Empty, ErrorNote, Failure, FIELD, FieldNote, Loading, Notice, Panel, Pill, QueueButton, Row, Section, type Load } from "./Parts.tsx";

const MAX_REASON = 500;

/** Refusals after which the list on screen is out of date, so it is read again. */
const RELOAD_AFTER = new Set(["payout_not_held", "release_already_proposed", "release_already_decided", "not_found"]);

interface HeldProps {
  api: ReviewQueueApi;
  directory: Directory;
  /** The signed-in member of staff, to tell their own proposals apart. */
  me: string;
  /** Whether this person holds `payout_accounts.change`. Without it they only look. */
  canDecide: boolean;
  now: () => Date;
  /** Makes the idempotency key for one step. Every try at the same step carries the same key. */
  newKey: () => string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCount?: (count: number) => void;
}

export type Stage = "needs_proposal" | "needs_second" | "proposed_by_me";

function stageOf(payout: HeldPayout, me: string): Stage {
  if (!payout.release) return "needs_proposal";
  return payout.release.proposedBy === me ? "proposed_by_me" : "needs_second";
}

export function HeldSection({ api, directory, me, canDecide, now, newKey, selectedId, onSelect, onCount }: HeldProps) {
  const [list, setList] = useState<Load<HeldPayout[]>>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null);
  // A payout just approved or declined leaves the list. It stays open with what happened to it.
  const [decided, setDecided] = useState<{ payout: HeldPayout; result: DecidedPayout } | null>(null);
  // The key for the step being tried on each payout, kept until the step or its reason changes.
  const pending = useRef(new Map<string, { request: string; key: string }>());

  const fetchList = useCallback(
    (): Promise<Load<HeldPayout[]>> =>
      api.listHeld().then(
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

  const items = list.state === "ready" ? list.value : [];
  const { clients, staff } = useNames(
    directory,
    [...items.map((item) => item.clientId), ...(decided ? [decided.payout.clientId] : [])],
    items.flatMap((item) => (item.release ? [item.release.proposedBy] : [])),
  );
  const who = (id: string) => (id === me ? "you" : (staff[id] ?? "a colleague"));
  // The payout named in the address, or the one just decided, or the one waiting longest.
  const shownId = selectedId ?? decided?.payout.id ?? items[0]?.id ?? null;
  const payout = items.find((item) => item.id === shownId) ?? (decided?.payout.id === shownId ? decided.payout : null);

  function keyFor(id: string, request: string): string {
    const waiting = pending.current.get(id);
    if (waiting?.request === request) return waiting.key;
    const key = newKey();
    pending.current.set(id, { request, key });
    return key;
  }

  async function act(target: HeldPayout, action: HeldAction, reason: string) {
    setBusy(true);
    setFailed(null);
    const key = () => keyFor(target.id, JSON.stringify({ action, release: target.release?.id ?? null, reason }));
    try {
      if (action === "propose") {
        await api.proposeRelease(target.id, reason, key());
      } else if (action === "withdraw" && target.release) {
        await api.withdrawRelease(target.release.id, reason);
      } else if (action === "approve" && target.release) {
        setDecided({ payout: target, result: await api.approveRelease(target.release.id, reason, key()) });
      } else if (action === "decline") {
        setDecided({ payout: target, result: await api.decline(target.id, reason, key()) });
      }
      pending.current.delete(target.id);
      await reloadList();
    } catch (error) {
      setFailed({ id: target.id, message: reviewQueueErrorMessage(error) });
      if (error instanceof ReviewQueueError && RELOAD_AFTER.has(error.code)) await reloadList();
    } finally {
      setBusy(false);
    }
  }

  if (list.state === "loading") return <Loading>Loading held payouts</Loading>;
  if (list.state === "failed") {
    return <Failure title="Held payouts could not be loaded" message={list.message} onRetry={() => void reloadList()} />;
  }
  if (items.length === 0 && !payout) {
    return (
      <Empty title="No payouts held">
        Nothing is waiting. A withdrawal above the review limit appears here, with its money already set aside, until two
        members of staff release it or one declines it.
      </Empty>
    );
  }

  return (
    <div className="flex flex-wrap items-start gap-5">
      <div className="min-w-0 flex-[1_1_260px]">
        {items.length === 0 ? (
          <p className="m-0 text-md text-muted">No other payouts are held.</p>
        ) : (
          <ul aria-label="Held payouts" className="m-0 flex list-none flex-col gap-2.5 p-0">
            {items.map((item) => {
              const stage = stageOf(item, me);
              return (
                <li key={item.id}>
                  <QueueButton
                    selected={item.id === shownId}
                    onSelect={() => {
                      setDecided(null);
                      onSelect(item.id);
                    }}
                  >
                    <span className="text-md font-bold tabular-nums">{formatAmount(item.amount)}</span>
                    <span className="text-xs text-muted">
                      {clientName(clients[item.clientId])}, {KIND_LABELS[item.kind].toLowerCase()}, asked for {timeAgo(item.createdAt, now())}
                    </span>
                    <span className={cn("mt-1 text-xs font-bold", stage === "proposed_by_me" ? "text-muted" : "text-warning-text")}>
                      {stage === "needs_proposal"
                        ? "Needs a proposal"
                        : stage === "proposed_by_me"
                          ? "Proposed by you"
                          : `Proposed by ${who(item.release!.proposedBy)}, needs a second person`}
                    </span>
                  </QueueButton>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div className="min-w-0 flex-[3_1_480px]">
        {payout ? (
          <HeldReview
            key={`${payout.id}:${payout.release?.id ?? ""}`}
            payout={payout}
            client={clients[payout.clientId]}
            stage={stageOf(payout, me)}
            who={who}
            result={decided?.payout.id === payout.id ? decided.result : null}
            busy={busy}
            error={failed?.id === payout.id ? failed.message : null}
            canDecide={canDecide}
            onAct={(action, reason) => void act(payout, action, reason)}
          />
        ) : (
          <Failure title="This payout is not held" message="It may have been decided already. Choose another from the list." />
        )}
      </div>
    </div>
  );
}

function clientName(card: ClientCard | null | undefined): string {
  return card?.name ?? "A client";
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const STAGE_PILLS: Record<Stage, string> = {
  needs_proposal: "Needs a proposal",
  needs_second: "Needs a second person",
  proposed_by_me: "Proposed by you",
};

function HeldReview({
  payout,
  client,
  stage,
  who,
  result,
  busy,
  error,
  canDecide,
  onAct,
}: {
  payout: HeldPayout;
  client: ClientCard | null | undefined;
  stage: Stage;
  who: (staffId: string) => string;
  result: DecidedPayout | null;
  busy: boolean;
  error: string | null;
  canDecide: boolean;
  onAct: (action: HeldAction, reason: string) => void;
}) {
  const name = clientName(client);
  return (
    <Panel label={`Held payout for ${name}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <h2 className="m-0 text-2xl font-extrabold tabular-nums">{formatAmount(payout.amount)}</h2>
          <span className="text-sm text-muted">
            To {name}
            {client?.accountNumber ? <span className="tabular-nums">, Finvest account {client.accountNumber}</span> : null}
          </span>
        </div>
        {result ? (
          <Pill tone={result.decision === "approved" ? "done" : "refused"}>{result.decision === "approved" ? "Released" : "Declined"}</Pill>
        ) : (
          <Pill tone={stage === "proposed_by_me" ? "quiet" : "waiting"}>{STAGE_PILLS[stage]}</Pill>
        )}
      </div>

      <div className="flex flex-wrap gap-5">
        <Section title="The withdrawal">
          <Row name="Paid to" value={KIND_LABELS[payout.kind]} />
          <Row name="Asked for" value={formatMoment(payout.createdAt)} />
          <Row name="Business day" value={formatDay(payout.businessDate)} />
          <Row name="Money" value="Set aside from the client's holding" />
        </Section>
      </div>

      {payout.release && !result ? (
        <div className="flex flex-wrap gap-5">
          <Section title="The proposal to release it">
            <Row name="Proposed by" value={`${capitalise(who(payout.release.proposedBy))}, ${formatMoment(payout.release.proposedAt)}`} />
            <Row name="Reason given" value={payout.release.reason} />
          </Section>
        </div>
      ) : null}

      <p className="m-0 text-md leading-normal text-muted">
        It is held because it is above the review limit. Releasing it takes two people: one proposes, a different person
        approves. One person may decline it.
      </p>

      {result ? (
        <Notice>
          {result.decision === "declined"
            ? "Declined. The payout is cancelled and its money went back where it came from."
            : payout.kind === "mobile_money"
              ? "Released. It is queued and the worker sends it to the provider."
              : "Released. It is queued for staff to pay from the trust account."}
        </Notice>
      ) : canDecide ? (
        <HeldForm stage={stage} busy={busy} error={error} onAct={onAct} />
      ) : (
        <p className="m-0 border-t border-divider pt-4 text-md leading-normal text-muted">
          You can look at this payout. Deciding it needs the payout accounts permission, held by Admin, Operations and
          Compliance.
        </p>
      )}
    </Panel>
  );
}

const BUTTONS: Record<HeldAction, string> = {
  propose: "Propose release",
  approve: "Approve release",
  withdraw: "Withdraw proposal",
  decline: "Decline",
};

/** Approving and declining cannot be undone, so each asks once more. Proposing and withdrawing move no money. */
export type Confirmations = Partial<Record<HeldAction, { text: string; confirm: string }>>;

const CONFIRMATIONS: Confirmations = {
  approve: { text: "Approving releases this money to be paid to the client. It cannot be undone.", confirm: "Yes, approve" },
  decline: { text: "Declining cancels the payout and gives the money back. It cannot be undone.", confirm: "Yes, decline" },
};

const PROPOSED_BY_ME =
  "You proposed releasing this payout, so a different person must approve it. You can withdraw your proposal or decline the payout.";

/** The reason, then propose, approve or withdraw, and decline. Shared with held transfers. */
export function HeldForm({
  stage,
  busy,
  error,
  onAct,
  confirmations = CONFIRMATIONS,
  proposedByMe = PROPOSED_BY_ME,
}: {
  stage: Stage;
  busy: boolean;
  error: string | null;
  onAct: (action: HeldAction, reason: string) => void;
  confirmations?: Confirmations;
  proposedByMe?: string;
}) {
  const ids = useId();
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | undefined>();
  const [confirming, setConfirming] = useState<HeldAction | null>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) confirmButton.current?.focus();
  }, [confirming]);

  const first: HeldAction = stage === "needs_proposal" ? "propose" : stage === "needs_second" ? "approve" : "withdraw";

  function attempt(action: HeldAction, confirmed = false) {
    if (!reason.trim()) {
      setProblem("Give the reason for your decision.");
      setConfirming(null);
      return;
    }
    setProblem(undefined);
    if (!confirmed && confirmations[action]) {
      setConfirming(action);
      return;
    }
    setConfirming(null);
    onAct(action, reason.trim());
  }

  const asked = confirming ? confirmations[confirming] : undefined;

  return (
    <form aria-label="Decision" className="flex flex-col gap-4 border-t border-divider pt-4" onSubmit={(event: FormEvent) => event.preventDefault()}>
      {stage === "proposed_by_me" ? (
        <Notice>{proposedByMe}</Notice>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${ids}-reason`}>Reason for your decision</Label>
        <Input
          id={`${ids}-reason`}
          value={reason}
          maxLength={MAX_REASON}
          onChange={(event) => setReason(event.target.value)}
          aria-invalid={problem ? true : undefined}
          aria-describedby={`${ids}-reason-help`}
          className={FIELD}
        />
        <FieldNote id={`${ids}-reason-help`} error={problem}>
          Kept in the audit trail. The client does not see it.
        </FieldNote>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {asked && confirming ? (
        <div role="alertdialog" aria-label="Confirm" className="flex flex-col gap-3 rounded-md bg-warning-surface p-3.5">
          <p className="m-0 text-md font-semibold text-warning-text">{asked.text}</p>
          <div className="flex flex-wrap gap-2.5">
            <Button ref={confirmButton} disabled={busy} onClick={() => attempt(confirming, true)}>
              {busy ? "Saving" : asked.confirm}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setConfirming(null)}>
              Go back
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2.5">
          <Button
            variant={first === "withdraw" ? "secondary" : "primary"}
            disabled={busy}
            onClick={() => attempt(first)}
            className="h-12 flex-[1_1_140px]"
          >
            {busy ? "Saving" : BUTTONS[first]}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => attempt("decline")} className="h-12 flex-[1_1_140px]">
            {BUTTONS.decline}
          </Button>
        </div>
      )}
    </form>
  );
}
