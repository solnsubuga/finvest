import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import type { ClientCard, Directory } from "@/directory/api.ts";
import {
  ReviewQueueError,
  reviewQueueErrorMessage,
  type DecidedPayout,
  type HeldDecision,
  type HeldPayout,
  type ReviewQueueApi,
} from "./api.ts";
import { formatAmount, formatDay, formatMoment, KIND_LABELS, timeAgo } from "./labels.ts";
import { useNames } from "./names.ts";
import { Empty, ErrorNote, Failure, FIELD, FieldNote, Loading, Notice, Panel, Pill, QueueButton, Row, Section, type Load } from "./Parts.tsx";

const MAX_REASON = 500;

interface HeldProps {
  api: ReviewQueueApi;
  directory: Directory;
  /** Whether this person holds `payout_accounts.change`. Without it they only look. */
  canDecide: boolean;
  now: () => Date;
  /** Makes the idempotency key for one decision. Every try at the same decision carries the same key. */
  newKey: () => string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCount?: (count: number) => void;
}

export function HeldSection({ api, directory, canDecide, now, newKey, selectedId, onSelect, onCount }: HeldProps) {
  const [list, setList] = useState<Load<HeldPayout[]>>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null);
  // A payout just decided leaves the list. It stays open with what happened to it.
  const [decided, setDecided] = useState<{ payout: HeldPayout; result: DecidedPayout } | null>(null);
  // The key for the decision being tried on each payout, kept until the decision or its reason changes.
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
  const { clients } = useNames(directory, [...items.map((item) => item.clientId), ...(decided ? [decided.payout.clientId] : [])], []);
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

  async function decide(target: HeldPayout, decision: HeldDecision, reason: string) {
    setBusy(true);
    setFailed(null);
    try {
      const result = await api.decideHeld(target.id, decision, reason, keyFor(target.id, JSON.stringify({ decision, reason })));
      pending.current.delete(target.id);
      setDecided({ payout: target, result });
      await reloadList();
    } catch (error) {
      setFailed({ id: target.id, message: reviewQueueErrorMessage(error) });
      if (error instanceof ReviewQueueError && (error.code === "payout_not_held" || error.code === "not_found")) await reloadList();
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
        Nothing is waiting. A withdrawal above the review limit appears here, with its money already set aside, until a
        member of staff approves or declines it.
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
            {items.map((item) => (
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
                    {clientName(clients[item.clientId])}, {KIND_LABELS[item.kind].toLowerCase()}
                  </span>
                  <span className="mt-1 text-xs font-bold text-warning-text">Asked for {timeAgo(item.createdAt, now())}</span>
                </QueueButton>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="min-w-0 flex-[3_1_480px]">
        {payout ? (
          <HeldReview
            key={payout.id}
            payout={payout}
            client={clients[payout.clientId]}
            result={decided?.payout.id === payout.id ? decided.result : null}
            busy={busy}
            error={failed?.id === payout.id ? failed.message : null}
            canDecide={canDecide}
            onDecide={(decision, reason) => void decide(payout, decision, reason)}
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

function HeldReview({
  payout,
  client,
  result,
  busy,
  error,
  canDecide,
  onDecide,
}: {
  payout: HeldPayout;
  client: ClientCard | null | undefined;
  result: DecidedPayout | null;
  busy: boolean;
  error: string | null;
  canDecide: boolean;
  onDecide: (decision: HeldDecision, reason: string) => void;
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
          <Pill tone={result.decision === "approved" ? "done" : "refused"}>{result.decision === "approved" ? "Approved" : "Declined"}</Pill>
        ) : (
          <Pill tone="waiting">Held for review</Pill>
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

      <p className="m-0 text-md leading-normal text-muted">
        It is held because it is above the review limit. The client asked for it, so you are the second person. The account
        it goes to was already approved by two people.
      </p>

      {result ? (
        <Notice>
          {result.decision === "declined"
            ? "Declined. The payout is cancelled and its money went back where it came from."
            : payout.kind === "mobile_money"
              ? "Approved. It is queued and the worker sends it to the provider."
              : "Approved. It is queued for staff to pay from the trust account."}
        </Notice>
      ) : canDecide ? (
        <HeldForm busy={busy} error={error} onDecide={onDecide} />
      ) : (
        <p className="m-0 border-t border-divider pt-4 text-md leading-normal text-muted">
          You can look at this payout. Deciding it needs the payout accounts permission, held by Admin, Operations and
          Compliance.
        </p>
      )}
    </Panel>
  );
}

const CONFIRMATIONS: Record<HeldDecision, { text: string; confirm: string }> = {
  approve: { text: "Approving releases this money to be paid to the client. It cannot be undone.", confirm: "Yes, approve" },
  decline: { text: "Declining cancels the payout and gives the money back. It cannot be undone.", confirm: "Yes, decline" },
};

function HeldForm({ busy, error, onDecide }: { busy: boolean; error: string | null; onDecide: (decision: HeldDecision, reason: string) => void }) {
  const ids = useId();
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | undefined>();
  const [confirming, setConfirming] = useState<HeldDecision | null>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) confirmButton.current?.focus();
  }, [confirming]);

  function attempt(decision: HeldDecision, confirmed = false) {
    if (!reason.trim()) {
      setProblem("Give the reason for your decision.");
      setConfirming(null);
      return;
    }
    setProblem(undefined);
    if (!confirmed) {
      setConfirming(decision);
      return;
    }
    setConfirming(null);
    onDecide(decision, reason.trim());
  }

  return (
    <form aria-label="Decision" className="flex flex-col gap-4 border-t border-divider pt-4" onSubmit={(event: FormEvent) => event.preventDefault()}>
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

      {confirming ? (
        <div role="alertdialog" aria-label="Confirm" className="flex flex-col gap-3 rounded-md bg-warning-surface p-3.5">
          <p className="m-0 text-md font-semibold text-warning-text">{CONFIRMATIONS[confirming].text}</p>
          <div className="flex flex-wrap gap-2.5">
            <Button ref={confirmButton} disabled={busy} onClick={() => attempt(confirming, true)}>
              {busy ? "Saving" : CONFIRMATIONS[confirming].confirm}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setConfirming(null)}>
              Go back
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2.5">
          <Button disabled={busy} onClick={() => attempt("approve")} className="h-12 flex-[1_1_140px]">
            {busy ? "Saving" : "Approve"}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => attempt("decline")} className="h-12 flex-[1_1_140px]">
            Decline
          </Button>
        </div>
      )}
    </form>
  );
}
