import { useCallback, useEffect, useRef, useState } from "react";
import type { ClientCard, Directory } from "@/directory/api.ts";
import { cn } from "@/lib.ts";
import type { HeldAction } from "./api.ts";
import { HeldForm, type Confirmations, type Stage } from "./Held.tsx";
import { formatAmount, formatMoment, timeAgo } from "./labels.ts";
import { useNames } from "./names.ts";
import { Empty, Failure, Loading, Notice, Panel, Pill, QueueButton, Row, Section, type Load } from "./Parts.tsx";
import { TransfersError, transfersErrorMessage, type HeldTransfer, type TransfersApi } from "./transfers.ts";

/** Refusals after which the list on screen is out of date, so it is read again. */
const RELOAD_AFTER = new Set(["conflict", "not_found"]);

const CONFIRMATIONS: Confirmations = {
  approve: {
    text: "Approving lets this investment move to the other client. It cannot be undone.",
    confirm: "Yes, approve",
  },
  decline: {
    text: "Declining stops this transfer. The sender keeps their investment and is told. It cannot be undone.",
    confirm: "Yes, decline",
  },
};

const PROPOSED_BY_ME =
  "You proposed letting this transfer through, so a different person must approve it. You can withdraw your proposal or decline the transfer.";

const STAGE_PILLS: Record<Stage, string> = {
  needs_proposal: "Needs a proposal",
  needs_second: "Needs a second person",
  proposed_by_me: "Proposed by you",
};

function stageOf(transfer: HeldTransfer, me: string): Stage {
  if (!transfer.release) return "needs_proposal";
  return transfer.release.proposedBy === me ? "proposed_by_me" : "needs_second";
}

interface TransfersProps {
  api: TransfersApi;
  directory: Directory;
  me: string;
  /** Whether this person holds `investment_transfers.change`. Without it they only look. */
  canDecide: boolean;
  now: () => Date;
  newKey: () => string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCount?: (count: number) => void;
}

/** Investments one client asked to send to another, held because they are large. */
export function TransfersSection({ api, directory, me, canDecide, now, newKey, selectedId, onSelect, onCount }: TransfersProps) {
  const [list, setList] = useState<Load<HeldTransfer[]>>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null);
  // A transfer just approved or declined leaves the list. It stays open with what happened.
  const [decided, setDecided] = useState<{ transfer: HeldTransfer; outcome: "approved" | "declined" } | null>(null);
  const pending = useRef(new Map<string, { request: string; key: string }>());

  const fetchList = useCallback(
    (): Promise<Load<HeldTransfer[]>> =>
      api.listHeld().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: transfersErrorMessage(error) }),
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
  const everyone = [...items, ...(decided ? [decided.transfer] : [])];
  const { clients, staff } = useNames(
    directory,
    everyone.flatMap((item) => [item.senderClientId, item.recipientClientId]),
    items.flatMap((item) => (item.release ? [item.release.proposedBy] : [])),
  );
  const who = (id: string) => (id === me ? "you" : (staff[id] ?? "a colleague"));
  const shownId = selectedId ?? decided?.transfer.id ?? items[0]?.id ?? null;
  const transfer = items.find((item) => item.id === shownId) ?? (decided?.transfer.id === shownId ? decided.transfer : null);

  function keyFor(id: string, request: string): string {
    const waiting = pending.current.get(id);
    if (waiting?.request === request) return waiting.key;
    const key = newKey();
    pending.current.set(id, { request, key });
    return key;
  }

  async function act(target: HeldTransfer, action: HeldAction, reason: string) {
    setBusy(true);
    setFailed(null);
    const key = () => keyFor(target.id, JSON.stringify({ action, proposed: target.release?.proposedAt ?? null, reason }));
    try {
      if (action === "propose") await api.proposeRelease(target.id, reason, key());
      else if (action === "withdraw") await api.withdrawRelease(target.id);
      else if (action === "approve") {
        await api.approveRelease(target.id, reason, key());
        setDecided({ transfer: target, outcome: "approved" });
      } else {
        await api.decline(target.id, reason, key());
        setDecided({ transfer: target, outcome: "declined" });
      }
      pending.current.delete(target.id);
      await reloadList();
    } catch (error) {
      setFailed({ id: target.id, message: transfersErrorMessage(error) });
      if (error instanceof TransfersError && RELOAD_AFTER.has(error.code)) await reloadList();
    } finally {
      setBusy(false);
    }
  }

  if (list.state === "loading") return <Loading>Loading held transfers</Loading>;
  if (list.state === "failed") {
    return <Failure title="Held transfers could not be loaded" message={list.message} onRetry={() => void reloadList()} />;
  }
  if (items.length === 0 && !transfer) {
    return (
      <Empty title="No transfers held">
        Nothing is waiting. An investment a client sends to another client appears here when it, with everything else they
        sent that day, is above the review limit, until two members of staff let it through or one declines it.
      </Empty>
    );
  }

  const name = (id: string) => clients[id]?.name ?? "A client";
  return (
    <div className="flex flex-wrap items-start gap-5">
      <div className="min-w-0 flex-[1_1_260px]">
        {items.length === 0 ? (
          <p className="m-0 text-md text-muted">No other transfers are held.</p>
        ) : (
          <ul aria-label="Held transfers" className="m-0 flex list-none flex-col gap-2.5 p-0">
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
                      {name(item.senderClientId)} to {name(item.recipientClientId)}, asked for {timeAgo(item.requestedAt, now())}
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
        {transfer ? (
          <TransferReview
            key={`${transfer.id}:${transfer.release?.proposedAt ?? ""}`}
            transfer={transfer}
            sender={clients[transfer.senderClientId]}
            recipient={clients[transfer.recipientClientId]}
            stage={stageOf(transfer, me)}
            who={who}
            outcome={decided?.transfer.id === transfer.id ? decided.outcome : null}
            busy={busy}
            error={failed?.id === transfer.id ? failed.message : null}
            canDecide={canDecide}
            onAct={(action, reason) => void act(transfer, action, reason)}
          />
        ) : (
          <Failure title="This transfer is not held" message="It may have been decided or cancelled already. Choose another from the list." />
        )}
      </div>
    </div>
  );
}

function person(card: ClientCard | null | undefined, shown: string): string {
  if (!card) return shown;
  return card.accountNumber ? `${card.name}, Finvest account ${card.accountNumber}` : card.name;
}

function TransferReview({
  transfer,
  sender,
  recipient,
  stage,
  who,
  outcome,
  busy,
  error,
  canDecide,
  onAct,
}: {
  transfer: HeldTransfer;
  sender: ClientCard | null | undefined;
  recipient: ClientCard | null | undefined;
  stage: Stage;
  who: (staffId: string) => string;
  outcome: "approved" | "declined" | null;
  busy: boolean;
  error: string | null;
  canDecide: boolean;
  onAct: (action: HeldAction, reason: string) => void;
}) {
  const proposer = transfer.release ? who(transfer.release.proposedBy) : "";
  return (
    <Panel label={`Held transfer from ${sender?.name ?? transfer.senderName}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <h2 className="m-0 text-2xl font-extrabold tabular-nums">{formatAmount(transfer.amount)}</h2>
          <span className="text-sm text-muted">{transfer.whole ? "The whole investment, with interest added until it moves" : "Part of an investment"}</span>
        </div>
        {outcome ? (
          <Pill tone={outcome === "approved" ? "done" : "refused"}>{outcome === "approved" ? "Let through" : "Declined"}</Pill>
        ) : (
          <Pill tone={stage === "proposed_by_me" ? "quiet" : "waiting"}>{STAGE_PILLS[stage]}</Pill>
        )}
      </div>

      <div className="flex flex-wrap gap-5">
        <Section title="The transfer">
          <Row name="From" value={person(sender, transfer.senderName)} />
          <Row name="To" value={person(recipient, transfer.recipientName)} />
          <Row name="Shown to each other as" value={`${transfer.senderName} and ${transfer.recipientName}`} />
          <Row name="Asked for" value={formatMoment(transfer.requestedAt)} />
          <Row name="Message" value={transfer.message ?? "None"} />
          <Row name="Money" value="Still in the sender's investment. Nothing moves until it is let through." />
        </Section>
      </div>

      {transfer.release && !outcome ? (
        <div className="flex flex-wrap gap-5">
          <Section title="The proposal to let it through">
            <Row name="Proposed by" value={`${proposer.charAt(0).toUpperCase()}${proposer.slice(1)}, ${formatMoment(transfer.release.proposedAt)}`} />
            <Row name="Reason given" value={transfer.release.reason} />
          </Section>
        </div>
      ) : null}

      <p className="m-0 text-md leading-normal text-muted">
        It is held because it, with everything else the sender sent that day, is above the review limit. Check that the
        sender means it and knows the recipient. Letting it through takes two people: one proposes, a different person
        approves. One person may decline it. The sender can cancel it until then.
      </p>

      {outcome ? (
        <Notice>
          {outcome === "declined"
            ? "Declined. Nothing moved, and the sender has been told."
            : "Let through. The worker moves it to the recipient in one ledger entry, and both clients are told."}
        </Notice>
      ) : canDecide ? (
        <HeldForm stage={stage} busy={busy} error={error} onAct={onAct} confirmations={CONFIRMATIONS} proposedByMe={PROPOSED_BY_ME} />
      ) : (
        <p className="m-0 border-t border-divider pt-4 text-md leading-normal text-muted">
          You can look at this transfer. Deciding it needs the investment transfers permission, held by Admin, Operations
          and Compliance.
        </p>
      )}
    </Panel>
  );
}
