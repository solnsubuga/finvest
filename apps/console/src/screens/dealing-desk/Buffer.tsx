import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import type { Directory } from "@/directory/api.ts";
import type { Currency } from "./api.ts";
import {
  BufferError,
  bufferErrorMessage,
  type BufferApi,
  type BufferScreen,
  type Movement,
  type NewMovement,
  type NewRules,
  type Rules,
  type RulesProposal,
} from "./buffer.ts";
import { bufferNote, formatDay, formatExact, formatShort, formatWhen, majorText, parseAmount } from "./format.ts";
import { FIELD, Field, FormButtons, NoteForm } from "./Orders.tsx";
import { Row } from "./Position.tsx";

const MAX_REASON = 500;

const MOVEMENT_STATUS: Record<Movement["status"], string> = {
  proposed: "Waiting for a second person",
  approved: "Approved and posted",
  declined: "Declined",
};

const PROPOSAL_STATUS: Record<RulesProposal["status"], string> = {
  proposed: "Waiting for a second person",
  approved: "Approved",
  sent_back: "Sent back",
  withdrawn: "Withdrawn",
};

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; screen: BufferScreen };
type Form = "pay_in" | "take_out" | "rules" | null;

interface BufferSectionProps {
  api: BufferApi;
  directory: Directory;
  me: string;
  currency?: Currency;
  /** Whether the person may propose or decide. The backend checks it again. */
  canChange: boolean;
  newKey?: () => string;
  /** The Kampala business day, as a calendar date. */
  today: () => string;
}

/** The buffer's rules and Finvest's own money in it. Each change takes two people. */
export function BufferSection({ api, directory, me, currency = "UGX", canChange, newKey = () => crypto.randomUUID(), today }: BufferSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [form, setForm] = useState<Form>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const pending = useRef(new Map<string, { request: string; key: string }>());

  const fetchScreen = useCallback(
    (): Promise<Load> =>
      api.getBuffer(currency).then(
        (screen) => ({ state: "ready", screen }),
        (error: unknown) => ({ state: "failed", message: bufferErrorMessage(error) }),
      ),
    [api, currency],
  );

  useEffect(() => {
    let current = true;
    void fetchScreen().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchScreen]);

  // The people named on the screen, looked up once each.
  const people = load.state === "ready" ? [...load.screen.movements, ...load.screen.proposals].flatMap((i) => [i.proposedBy, i.decidedBy ?? ""]) : [];
  const missing = [...new Set(people)].filter((id) => id && id !== me && !(id in names)).join(",");
  useEffect(() => {
    if (!missing) return;
    let current = true;
    void Promise.all(missing.split(",").map(async (id) => [id, (await directory.staffName(id)) ?? "A colleague"] as const)).then(
      (found) => current && setNames((known) => ({ ...known, ...Object.fromEntries(found) })),
    );
    return () => {
      current = false;
    };
  }, [missing, directory]);
  const who = (id: string) => (id === me ? "You" : (names[id] ?? "A colleague"));

  function keyFor(on: string, request: string): string {
    const waiting = pending.current.get(on);
    if (waiting?.request === request) return waiting.key;
    const key = newKey();
    pending.current.set(on, { request, key });
    return key;
  }

  function cancel(on: string) {
    pending.current.delete(on);
    setFailed((last) => (last?.on === on ? null : last));
  }

  async function run(on: string, request: unknown, command: (key: string) => Promise<BufferScreen>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      setLoad({ state: "ready", screen: await command(keyFor(on, JSON.stringify(request))) });
      pending.current.delete(on);
      return true;
    } catch (error) {
      if (error instanceof BufferError && error.code !== "unavailable") pending.current.delete(on);
      setFailed({ on, message: bufferErrorMessage(error) });
      if (error instanceof BufferError && (error.code === "conflict" || error.code === "not_found")) {
        const fresh = await fetchScreen();
        if (fresh.state === "ready") setLoad(fresh);
      }
      return false;
    } finally {
      setBusy(null);
    }
  }

  const heading = (
    <h2 id="buffer-heading" className="m-0 text-xl font-extrabold">
      Liquidity buffer
    </h2>
  );

  if (load.state === "loading") {
    return (
      <section aria-labelledby="buffer-heading" className="flex flex-col gap-3">
        {heading}
        <p role="status" className="m-0 text-md text-muted">
          Loading the buffer
        </p>
      </section>
    );
  }
  if (load.state === "failed") {
    return (
      <section aria-labelledby="buffer-heading" className="flex flex-col gap-3">
        {heading}
        <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">The buffer could not be loaded</h3>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchScreen().then(setLoad)}>
            Try again
          </Button>
        </div>
      </section>
    );
  }

  const { screen } = load;
  const upcoming = screen.versions.filter((v) => v.effectiveFrom > today());
  const closeForm = (on: string) => {
    setForm(null);
    cancel(on);
  };

  return (
    <section aria-labelledby="buffer-heading" className="flex flex-col gap-4">
      <div>
        {heading}
        <p className="m-0 text-md leading-normal text-muted">
          Finvest's own money in the trust account pays small early withdrawals at once. Every change here is approved by a second person.
        </p>
      </div>
      <div className="flex flex-wrap items-start gap-5">
        <section aria-labelledby="finvest-money" className="flex min-w-0 flex-[3_1_420px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 id="finvest-money" className="m-0 text-[17px] font-extrabold">
                Finvest's money in the buffer
              </h3>
              <p className="m-0 text-[28px] font-extrabold tabular-nums">{formatExact(screen.finvestMoney)}</p>
              <p className="m-0 text-sm text-muted">
                Buffer after promised withdrawals {formatShort(screen.buffer)}. {bufferNote(screen.level, screen.inForce.target, screen.inForce.floor)}
              </p>
            </div>
            {canChange && form === null ? (
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => setForm("pay_in")}>Record money paid in</Button>
                <Button variant="secondary" onClick={() => setForm("take_out")}>
                  Take money out
                </Button>
              </div>
            ) : null}
          </div>
          {form === "pay_in" || form === "take_out" ? (
            <MovementForm
              direction={form === "pay_in" ? "in" : "out"}
              currency={screen.currency}
              today={today()}
              busy={busy === "new-movement"}
              error={failed?.on === "new-movement" ? failed.message : null}
              onCancel={() => closeForm("new-movement")}
              onSubmit={(movement) =>
                void run("new-movement", movement, (key) => api.proposeMovement(key, movement)).then((done) => done && setForm(null))
              }
            />
          ) : null}
          {screen.movements.length === 0 ? (
            <p className="m-0 rounded-md bg-surface-sunken px-3 py-2.5 text-md leading-normal">
              Finvest has not paid any money into the buffer yet. Until it does, every early withdrawal waits for bills to be sold.
            </p>
          ) : (
            <ul aria-label="Money paid in and taken out" className="m-0 flex list-none flex-col gap-3 p-0">
              {screen.movements.map((movement) => (
                <MovementItem
                  today={today()}
                  key={`${movement.id}:${movement.status}`}
                  movement={movement}
                  who={who}
                  busy={busy === movement.id}
                  error={failed?.on === movement.id ? failed.message : null}
                  onApprove={() => void run(movement.id, "approve", (key) => api.approveMovement(key, movement.id))}
                  onDecline={(reason) => void run(movement.id, { reason }, (key) => api.declineMovement(key, movement.id, reason))}
                  onCancel={() => cancel(movement.id)}
                />
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="buffer-rules-changes" className="flex min-w-0 flex-[2_1_300px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <h3 id="buffer-rules-changes" className="m-0 text-[17px] font-extrabold">
              Buffer rules
            </h3>
            {canChange && form === null ? (
              <Button variant="secondary" onClick={() => setForm("rules")}>
                Propose new rules
              </Button>
            ) : null}
          </div>
          <p className="m-0 text-xs font-bold text-muted">In force since {formatDay(screen.inForce.effectiveFrom)}</p>
          <RulesRows rules={screen.inForce} />
          {upcoming.map((rules) => (
            <div key={rules.effectiveFrom} className="flex flex-col gap-2">
              <p className="m-0 text-xs font-bold text-muted">Approved, from {formatDay(rules.effectiveFrom)}</p>
              <RulesRows rules={rules} />
            </div>
          ))}
          {form === "rules" ? (
            <RulesForm
              current={screen.inForce}
              today={today()}
              busy={busy === "new-rules"}
              error={failed?.on === "new-rules" ? failed.message : null}
              onCancel={() => closeForm("new-rules")}
              onSubmit={(rules) => void run("new-rules", rules, (key) => api.proposeRules(key, rules)).then((done) => done && setForm(null))}
            />
          ) : null}
          {screen.proposals.length === 0 ? null : (
            <ul aria-label="Changes to the rules" className="m-0 flex list-none flex-col gap-3 p-0">
              {screen.proposals.map((proposal) => (
                <ProposalItem
                  key={`${proposal.id}:${proposal.status}`}
                  proposal={proposal}
                  who={who}
                  busy={busy === proposal.id}
                  error={failed?.on === proposal.id ? failed.message : null}
                  onApprove={() => void run(proposal.id, "approve", (key) => api.approveRules(key, proposal.id))}
                  onWithdraw={() => void run(proposal.id, "withdraw", (key) => api.withdrawRules(key, proposal.id))}
                  onSendBack={(note) => void run(proposal.id, { note }, (key) => api.sendBackRules(key, proposal.id, note))}
                  onCancel={() => cancel(proposal.id)}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </section>
  );
}

function RulesRows({ rules }: { rules: Rules }) {
  const days = rules.payWithinWorkingDays === 1 ? "1 working day" : `${rules.payWithinWorkingDays} working days`;
  return (
    <dl className="m-0 flex flex-col gap-2.5">
      <Row name="Target" value={formatExact(rules.target)} />
      <Row name="Floor" value={formatExact(rules.floor)} />
      <Row name="From the buffer, each withdrawal" value={`Up to ${formatExact(rules.fromBufferUpTo)}`} />
      <Row name="Client waiting for a sale is paid within" value={days} />
    </dl>
  );
}

function ErrorLine({ message }: { message: string | null }) {
  return message ? (
    <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2 text-sm font-bold text-danger">
      {message}
    </p>
  ) : null;
}

interface MovementItemProps {
  movement: Movement;
  today: string;
  who: (id: string) => string;
  busy: boolean;
  error: string | null;
  onApprove: () => void;
  onDecline: (reason: string) => void;
  onCancel: () => void;
}

function MovementItem({ movement, today, who, busy, error, onApprove, onDecline, onCancel }: MovementItemProps) {
  const [declining, setDeclining] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const paysIn = movement.direction === "in";
  const waiting = movement.status === "proposed";
  return (
    <li className="flex flex-col gap-2 border-t border-divider pt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="m-0 text-md font-extrabold">
          {paysIn ? "Paid in" : "Taken out"} {formatExact(movement.amount)}
        </p>
        <p className="m-0 text-xs font-bold text-muted">{MOVEMENT_STATUS[movement.status]}</p>
      </div>
      <dl className="m-0 flex flex-col gap-2">
        {paysIn ? <Row name="Bank reference" value={movement.bankReference ?? ""} /> : <Row name="Transfer reference" value={movement.id} />}
        {movement.valueDate ? <Row name={paysIn ? "Arrived" : "Leaves"} value={formatDay(movement.valueDate)} /> : null}
        <Row name="Recorded by" value={`${who(movement.proposedBy)}, ${formatWhen(movement.proposedAt)}`} />
        {movement.decidedBy ? <Row name={movement.status === "declined" ? "Declined by" : "Approved by"} value={who(movement.decidedBy)} /> : null}
      </dl>
      <p className="m-0 text-sm leading-[1.45] text-muted">{movement.reason}</p>
      {movement.decisionReason ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3 py-2 text-sm leading-[1.45]">
          <strong>Declined:</strong> {movement.decisionReason}
        </p>
      ) : null}
      {waiting && movement.allowed.length === 0 ? (
        <p className="m-0 text-sm text-muted">You recorded this, so someone else has to approve it.</p>
      ) : null}
      {!paysIn && movement.status === "approved" && movement.valueDate === today ? (
        <p className="m-0 text-sm text-muted">Make the transfer from the trust account today, quoting the transfer reference.</p>
      ) : null}
      <ErrorLine message={error} />
      {declining ? (
        <NoteForm
          label="Why are you declining it?"
          submit="Decline"
          busy={busy}
          onCancel={() => {
            setDeclining(false);
            onCancel();
          }}
          onSubmit={onDecline}
        />
      ) : confirming ? (
        <div role="group" aria-label="Confirm the approval" className="flex flex-col gap-2">
          <p className="m-0 text-md font-bold leading-normal">
            {paysIn
              ? `Does the trust account statement show ${formatExact(movement.amount)} arriving with reference ${movement.bankReference ?? ""}?`
              : `Approve taking ${formatExact(movement.amount)} of Finvest's money out of the trust account today?`}
          </p>
          <p className="m-0 text-sm leading-[1.45] text-muted">This posts it to the ledger. A ledger entry is never edited afterwards.</p>
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy} onClick={onApprove}>
              Yes, approve and post
            </Button>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                onCancel();
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : movement.allowed.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {movement.allowed.includes("approve") ? (
            <Button disabled={busy} onClick={() => setConfirming(true)}>
              Approve as second person
            </Button>
          ) : null}
          {movement.allowed.includes("decline") ? (
            <Button variant="secondary" disabled={busy} onClick={() => setDeclining(true)}>
              Decline
            </Button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

interface ProposalItemProps {
  proposal: RulesProposal;
  who: (id: string) => string;
  busy: boolean;
  error: string | null;
  onApprove: () => void;
  onWithdraw: () => void;
  onSendBack: (note: string) => void;
  onCancel: () => void;
}

function ProposalItem({ proposal, who, busy, error, onApprove, onWithdraw, onSendBack, onCancel }: ProposalItemProps) {
  const [sendingBack, setSendingBack] = useState(false);
  const can = (step: RulesProposal["allowed"][number]) => proposal.allowed.includes(step);
  return (
    <li className="flex flex-col gap-2 border-t border-divider pt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="m-0 text-md font-extrabold">New rules from {formatDay(proposal.rules.effectiveFrom)}</p>
        <p className="m-0 text-xs font-bold text-muted">{PROPOSAL_STATUS[proposal.status]}</p>
      </div>
      <RulesRows rules={proposal.rules} />
      <dl className="m-0 flex flex-col">
        <Row name="Proposed by" value={`${who(proposal.proposedBy)}, ${formatWhen(proposal.proposedAt)}`} />
      </dl>
      <p className="m-0 text-sm leading-[1.45] text-muted">{proposal.reason}</p>
      {proposal.decisionNote ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3 py-2 text-sm leading-[1.45]">
          <strong>Sent back:</strong> {proposal.decisionNote}
        </p>
      ) : null}
      <ErrorLine message={error} />
      {sendingBack ? (
        <NoteForm
          label="Why are you sending it back?"
          submit="Send back"
          busy={busy}
          onCancel={() => {
            setSendingBack(false);
            onCancel();
          }}
          onSubmit={onSendBack}
        />
      ) : (
        <div className="flex flex-wrap gap-2">
          {can("approve") ? (
            <Button disabled={busy} onClick={onApprove}>
              Approve as second person
            </Button>
          ) : null}
          {can("send_back") ? (
            <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(true)}>
              Send back
            </Button>
          ) : null}
          {can("withdraw") ? (
            <Button variant="secondary" disabled={busy} onClick={onWithdraw}>
              Withdraw
            </Button>
          ) : null}
        </div>
      )}
    </li>
  );
}

interface MovementFormProps {
  direction: "in" | "out";
  currency: Currency;
  today: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: (movement: NewMovement) => void;
}

type MovementErrors = Partial<Record<"amount" | "reference" | "valueDate" | "reason", string>>;

function MovementForm({ direction, currency, today, busy, error, onCancel, onSubmit }: MovementFormProps) {
  const id = useId();
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [valueDate, setValueDate] = useState(today);
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<MovementErrors>({});
  const paysIn = direction === "in";

  function submit(event: FormEvent) {
    event.preventDefault();
    const parsed = parseAmount(amount, currency);
    const found: MovementErrors = {};
    if (!parsed) found.amount = `Enter a whole amount in ${currency}.`;
    if (paysIn && !reference.trim()) found.reference = "Enter the bank's reference for the transfer.";
    if (paysIn && (!valueDate || valueDate > today)) found.valueDate = "Enter the day it arrived, today or earlier.";
    if (!reason.trim()) found.reason = "Say why, so the second person can check it.";
    setErrors(found);
    if (Object.keys(found).length > 0 || !parsed) return;
    onSubmit(
      paysIn
        ? { direction: "in", amount: parsed, reason: reason.trim(), bankReference: reference.trim(), valueDate }
        : { direction: "out", amount: parsed, reason: reason.trim() },
    );
  }

  return (
    <form noValidate onSubmit={submit} aria-label={paysIn ? "Record money paid in" : "Take money out"} className="flex flex-col gap-3 rounded-lg bg-surface-sunken p-4">
      <p className="m-0 text-md font-bold leading-normal">
        {paysIn
          ? "Record Finvest's money once it is on the trust account. A second person checks it against the statement."
          : "Propose it before any money leaves. Once a second person approves, transfer it quoting the reference shown."}
      </p>
      <Field id={`${id}-amount`} label={`Amount (${currency})`} error={errors.amount}>
        {(control) => <Input {...control} inputMode="numeric" className={FIELD} value={amount} onChange={(e) => setAmount(e.target.value)} />}
      </Field>
      {paysIn ? (
        <>
          <Field id={`${id}-reference`} label="Bank reference" error={errors.reference}>
            {(control) => <Input {...control} maxLength={100} className={FIELD} value={reference} onChange={(e) => setReference(e.target.value)} />}
          </Field>
          <Field id={`${id}-date`} label="Arrived on" error={errors.valueDate}>
            {(control) => <Input {...control} type="date" max={today} className={FIELD} value={valueDate} onChange={(e) => setValueDate(e.target.value)} />}
          </Field>
        </>
      ) : null}
      <Field id={`${id}-reason`} label="Reason" error={errors.reason}>
        {(control) => (
          <textarea
            {...control}
            rows={2}
            maxLength={MAX_REASON}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="w-full rounded-lg border border-border-strong bg-surface px-3.5 py-2.5 text-md text-ink aria-invalid:border-danger"
          />
        )}
      </Field>
      <ErrorLine message={error} />
      <FormButtons submit={paysIn ? "Record for approval" : "Propose for approval"} busy={busy} onCancel={onCancel} />
    </form>
  );
}

interface RulesFormProps {
  current: Rules;
  today: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: (rules: NewRules) => void;
}

type RulesErrors = Partial<Record<"effectiveFrom" | "target" | "floor" | "upTo" | "days" | "reason", string>>;


function RulesForm({ current, today, busy, error, onCancel, onSubmit }: RulesFormProps) {
  const id = useId();
  const currency = current.target.currency;
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [target, setTarget] = useState(majorText(current.target.minor, currency));
  const [floor, setFloor] = useState(majorText(current.floor.minor, currency));
  const [upTo, setUpTo] = useState(majorText(current.fromBufferUpTo.minor, currency));
  const [days, setDays] = useState(String(current.payWithinWorkingDays));
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<RulesErrors>({});

  function submit(event: FormEvent) {
    event.preventDefault();
    const found: RulesErrors = {};
    const amounts = { target: parseAmount(target, currency), floor: parseAmount(floor, currency), upTo: parseAmount(upTo, currency) };
    if (!effectiveFrom || effectiveFrom <= today) found.effectiveFrom = "New rules start on a later day than today.";
    for (const [field, value] of Object.entries(amounts)) if (!value) found[field as "target"] = `Enter a whole amount in ${currency}.`;
    const working = Number(days);
    if (!Number.isInteger(working) || working < 1 || working > 30) found.days = "Enter between 1 and 30 working days.";
    if (!reason.trim()) found.reason = "Say why, so the second person can check it.";
    setErrors(found);
    if (Object.keys(found).length > 0 || !amounts.target || !amounts.floor || !amounts.upTo) return;
    onSubmit({
      effectiveFrom,
      target: amounts.target,
      floor: amounts.floor,
      fromBufferUpTo: amounts.upTo,
      payWithinWorkingDays: working,
      reason: reason.trim(),
    });
  }

  const amountField = (key: "target" | "floor" | "upTo", label: string, value: string, set: (text: string) => void) => (
    <Field id={`${id}-${key}`} label={label} error={errors[key]}>
      {(control) => <Input {...control} inputMode="numeric" className={FIELD} value={value} onChange={(e) => set(e.target.value)} />}
    </Field>
  );

  return (
    <form noValidate onSubmit={submit} aria-label="Propose new rules" className="flex flex-col gap-3 rounded-lg bg-surface-sunken p-4">
      <p className="m-0 text-md font-bold leading-normal">New rules start on a later day, once a second person approves them.</p>
      <Field id={`${id}-from`} label="Starts on" error={errors.effectiveFrom}>
        {(control) => <Input {...control} type="date" min={today} className={FIELD} value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} />}
      </Field>
      {amountField("target", `Target (${currency})`, target, setTarget)}
      {amountField("floor", `Floor (${currency})`, floor, setFloor)}
      {amountField("upTo", `From the buffer, each withdrawal (${currency})`, upTo, setUpTo)}
      <Field id={`${id}-days`} label="Client waiting for a sale is paid within (working days)" error={errors.days}>
        {(control) => <Input {...control} inputMode="numeric" className={FIELD} value={days} onChange={(e) => setDays(e.target.value)} />}
      </Field>
      <Field id={`${id}-reason`} label="Reason" error={errors.reason}>
        {(control) => (
          <textarea
            {...control}
            rows={2}
            maxLength={MAX_REASON}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="w-full rounded-lg border border-border-strong bg-surface px-3.5 py-2.5 text-md text-ink aria-invalid:border-danger"
          />
        )}
      </Field>
      <ErrorLine message={error} />
      <FormButtons submit="Propose for approval" busy={busy} onCancel={onCancel} />
    </form>
  );
}
