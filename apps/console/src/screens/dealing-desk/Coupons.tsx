import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { DateInput } from "@/components/ui/date-input.tsx";
import type { Directory } from "@/directory/api.ts";
import type { Currency } from "./api.ts";
import { COUPON_STATUS_LABELS, CouponError, couponErrorMessage, type Coupon, type CouponDue, type CouponsApi, type CouponsScreen, type NewCoupon } from "./coupons.ts";
import { formatDay, formatExact, formatWhen, majorText, parseAmount } from "./format.ts";
import { FIELD, Field, FormButtons, NoteForm } from "./Orders.tsx";
import { Row } from "./Position.tsx";

const MAX_NOTE = 500;

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; screen: CouponsScreen };

interface CouponsSectionProps {
  api: CouponsApi;
  directory: Directory;
  me: string;
  currency?: Currency;
  newKey?: () => string;
  /** The Kampala business day, as a calendar date. */
  today: () => string;
}

const dueKey = (due: CouponDue) => `due:${due.issueCode}:${due.couponDate}`;

/** Coupons Bank of Uganda pays on the desk's own bonds. One person records each, a second approves it. */
export function CouponsSection({ api, directory, me, currency = "UGX", newKey = () => crypto.randomUUID(), today }: CouponsSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [recording, setRecording] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const pending = useRef(new Map<string, { request: string; key: string }>());

  const fetchScreen = useCallback(
    (): Promise<Load> =>
      api.getCoupons(currency).then(
        (screen) => ({ state: "ready", screen }),
        (error: unknown) => ({ state: "failed", message: couponErrorMessage(error) }),
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
  const people = load.state === "ready" ? load.screen.coupons.flatMap((c) => [c.recordedBy, c.decidedBy ?? ""]) : [];
  const missing = [...new Set(people)].filter((id) => id && id !== me && !(id in names)).join(",");
  useEffect(() => {
    if (!missing) return;
    let current = true;
    void Promise.all(
      missing.split(",").map(async (id) => [id, (await directory.staffName(id).catch(() => null)) ?? "A colleague"] as const),
    ).then((found) => current && setNames((known) => ({ ...known, ...Object.fromEntries(found) })));
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

  async function run(on: string, request: unknown, command: (key: string) => Promise<CouponsScreen>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      setLoad({ state: "ready", screen: await command(keyFor(on, JSON.stringify(request))) });
      pending.current.delete(on);
      return true;
    } catch (error) {
      // Only an answer that never arrived leaves the command open. A refusal is an answer.
      if (error instanceof CouponError && error.code !== "unavailable") pending.current.delete(on);
      setFailed({ on, message: couponErrorMessage(error) });
      if (error instanceof CouponError && (error.code === "conflict" || error.code === "not_found")) {
        const fresh = await fetchScreen();
        if (fresh.state === "ready") setLoad(fresh);
      }
      return false;
    } finally {
      setBusy(null);
    }
  }

  const heading = (
    <div>
      <h2 id="coupons-heading" className="m-0 text-xl font-extrabold">
        Coupons on the desk's bonds
      </h2>
      <p className="m-0 text-md leading-normal text-muted">
        Bank of Uganda pays the desk a coupon on each bond it holds. Record what reached the trust account. A second person checks the bank statement
        and approves it.
      </p>
    </div>
  );

  if (load.state === "loading") {
    return (
      <section aria-labelledby="coupons-heading" className="flex flex-col gap-3">
        {heading}
        <p role="status" className="m-0 text-md text-muted">
          Loading coupons
        </p>
      </section>
    );
  }
  if (load.state === "failed") {
    return (
      <section aria-labelledby="coupons-heading" className="flex flex-col gap-3">
        {heading}
        <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">Coupons could not be loaded</h3>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchScreen().then(setLoad)}>
            Try again
          </Button>
        </div>
      </section>
    );
  }

  const { screen } = load;
  return (
    <section aria-labelledby="coupons-heading" className="flex flex-col gap-4">
      {heading}
      <div className="flex flex-wrap items-start gap-5">
        <section aria-labelledby="coupons-due" className="flex min-w-0 flex-[2_1_340px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 id="coupons-due" className="m-0 text-[17px] font-extrabold">
            Due to record
          </h3>
          {screen.due.length === 0 ? (
            <p className="m-0 rounded-md bg-surface-sunken px-3 py-2.5 text-md leading-normal">
              Every coupon the desk's bonds have earned so far is recorded.
            </p>
          ) : (
            <ul aria-label="Coupons due to record" className="m-0 flex list-none flex-col gap-3 p-0">
              {screen.due.map((due) => {
                const on = dueKey(due);
                return (
                  <li key={on} className="flex flex-col gap-2 border-t border-divider pt-3">
                    <p className="m-0 text-md font-extrabold">
                      {due.name}, coupon of {formatDay(due.couponDate)}
                    </p>
                    <dl className="m-0 flex flex-col gap-2">
                      <Row name="Face value held" value={formatExact(due.face)} />
                      <Row name="Earns, before any tax" value={formatExact(due.expected)} />
                    </dl>
                    {recording === on ? (
                      <RecordForm
                        due={due}
                        today={today()}
                        busy={busy === on}
                        error={failed?.on === on ? failed.message : null}
                        onCancel={() => {
                          setRecording(null);
                          cancel(on);
                        }}
                        onSubmit={(coupon) => void run(on, coupon, (key) => api.record(key, coupon)).then((done) => done && setRecording(null))}
                      />
                    ) : screen.mayChange && recording === null ? (
                      <div>
                        <Button onClick={() => setRecording(on)}>Record what arrived</Button>
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
        <section aria-labelledby="coupons-recorded" className="flex min-w-0 flex-[3_1_420px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 id="coupons-recorded" className="m-0 text-[17px] font-extrabold">
            Recorded
          </h3>
          {screen.coupons.length === 0 ? (
            <p className="m-0 rounded-md bg-surface-sunken px-3 py-2.5 text-md leading-normal">No coupon has been recorded yet.</p>
          ) : (
            <ul aria-label="Coupons recorded" className="m-0 flex list-none flex-col gap-3 p-0">
              {screen.coupons.map((coupon) => (
                <CouponItem
                  key={`${coupon.id}:${coupon.status}`}
                  coupon={coupon}
                  who={who}
                  busy={busy === coupon.id}
                  error={failed?.on === coupon.id ? failed.message : null}
                  onApprove={() => void run(coupon.id, "approve", (key) => api.approve(key, coupon.id))}
                  onDecline={(reason) => void run(coupon.id, { reason }, (key) => api.decline(key, coupon.id, reason))}
                  onCancel={() => cancel(coupon.id)}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </section>
  );
}

function ErrorLine({ message }: { message: string | null }) {
  return message ? (
    <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2 text-sm font-bold text-danger">
      {message}
    </p>
  ) : null;
}

interface CouponItemProps {
  coupon: Coupon;
  who: (id: string) => string;
  busy: boolean;
  error: string | null;
  onApprove: () => void;
  onDecline: (reason: string) => void;
  onCancel: () => void;
}

function CouponItem({ coupon, who, busy, error, onApprove, onDecline, onCancel }: CouponItemProps) {
  const [declining, setDeclining] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const waiting = coupon.status === "recorded";
  const differs = coupon.amount.minor !== coupon.expected.minor;
  return (
    <li aria-label={`${coupon.issueCode} coupon of ${formatDay(coupon.couponDate)}`} className="flex flex-col gap-2 border-t border-divider pt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="m-0 text-md font-extrabold">
          {coupon.issueCode}, coupon of {formatDay(coupon.couponDate)}
        </p>
        <p className="m-0 text-xs font-bold text-muted">{COUPON_STATUS_LABELS[coupon.status]}</p>
      </div>
      <dl className="m-0 flex flex-col gap-2">
        <Row name="Arrived" value={`${formatExact(coupon.amount)} on ${formatDay(coupon.valueDate)}`} />
        <Row name="Earns, before any tax" value={formatExact(coupon.expected)} />
        <Row name="Face value held" value={formatExact(coupon.face)} />
        <Row name="Bank reference" value={coupon.bankReference} />
        <Row name="Recorded by" value={`${who(coupon.recordedBy)}, ${formatWhen(coupon.recordedAt)}`} />
        {coupon.decidedBy ? <Row name={coupon.status === "declined" ? "Declined by" : "Approved by"} value={who(coupon.decidedBy)} /> : null}
      </dl>
      {coupon.note ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3 py-2 text-sm leading-[1.45]">
          <strong>{differs ? "Why it differs:" : "Note:"}</strong> {coupon.note}
        </p>
      ) : null}
      {coupon.decisionReason ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3 py-2 text-sm leading-[1.45]">
          <strong>Declined:</strong> {coupon.decisionReason}
        </p>
      ) : null}
      {waiting && !coupon.allowed.includes("approve") && coupon.allowed.includes("decline") ? (
        <p className="m-0 text-sm text-muted">You recorded this, so someone else has to approve it.</p>
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
            Does the trust account statement show {formatExact(coupon.amount)} arriving on {formatDay(coupon.valueDate)} with reference{" "}
            {coupon.bankReference}?
          </p>
          <p className="m-0 text-sm leading-[1.45] text-muted">This posts it to the ledger as Finvest's income. A ledger entry is never edited afterwards.</p>
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
      ) : coupon.allowed.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {coupon.allowed.includes("approve") ? (
            <Button disabled={busy} onClick={() => setConfirming(true)}>
              Approve as second person
            </Button>
          ) : null}
          {coupon.allowed.includes("decline") ? (
            <Button variant="secondary" disabled={busy} onClick={() => setDeclining(true)}>
              Decline
            </Button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

type RecordErrors = Partial<Record<"amount" | "valueDate" | "reference" | "note", string>>;

interface RecordFormProps {
  due: CouponDue;
  today: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: (coupon: NewCoupon) => void;
}

/** What the custodian's note and the bank statement say arrived for one coupon. */
function RecordForm({ due, today, busy, error, onCancel, onSubmit }: RecordFormProps) {
  const id = useId();
  const currency = due.expected.currency;
  const [amount, setAmount] = useState(majorText(due.expected.minor, currency));
  const [valueDate, setValueDate] = useState(today);
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<RecordErrors>({});
  const typed = parseAmount(amount, currency);
  const differs = typed !== null && typed.minor !== due.expected.minor;

  function submit(event: FormEvent) {
    event.preventDefault();
    const found: RecordErrors = {};
    if (!typed) found.amount = "Enter what arrived as a whole number.";
    if (!valueDate) found.valueDate = "Enter the day it reached the trust account.";
    else if (valueDate < due.couponDate) found.valueDate = `The money arrives on or after the coupon date, ${formatDay(due.couponDate)}.`;
    else if (valueDate > today) found.valueDate = "Record a coupon once it is on the trust account, on or before today.";
    if (!reference.trim()) found.reference = "Enter the bank's reference for the payment.";
    if (differs && !note.trim()) found.note = "What arrived differs from what the bonds earn. Say why.";
    setErrors(found);
    if (Object.keys(found).length > 0 || !typed) return;
    onSubmit({
      issueCode: due.issueCode,
      couponDate: due.couponDate,
      amount: typed,
      valueDate,
      bankReference: reference.trim(),
      note: note.trim() ? note.trim() : null,
    });
  }

  return (
    <form noValidate onSubmit={submit} aria-label="Record the coupon" className="flex flex-col gap-3 border-t border-divider pt-3">
      <Field id={`${id}-amount`} label={`What arrived (${currency})`} error={errors.amount} hint="As the bank statement shows it.">
        {(control) => <Input {...control} className={FIELD} inputMode="numeric" value={amount} onChange={(event) => setAmount(event.target.value)} />}
      </Field>
      <Field id={`${id}-date`} label="Arrived on" error={errors.valueDate}>
        {(control) => (
          <DateInput {...control} className={FIELD} min={due.couponDate} max={today} value={valueDate} onChange={(event) => setValueDate(event.target.value)} />
        )}
      </Field>
      <Field id={`${id}-reference`} label="Bank reference" error={errors.reference}>
        {(control) => <Input {...control} className={FIELD} maxLength={100} value={reference} onChange={(event) => setReference(event.target.value)} />}
      </Field>
      <Field
        id={`${id}-note`}
        label={differs ? "Why it differs" : "Note (optional)"}
        error={errors.note}
        {...(differs ? { hint: `The bonds earn ${formatExact(due.expected)} before any tax.` } : {})}
      >
        {(control) => (
          <textarea
            {...control}
            rows={2}
            maxLength={MAX_NOTE}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            className="w-full rounded-lg border border-border-strong bg-surface px-3.5 py-2.5 text-md text-ink aria-invalid:border-danger"
          />
        )}
      </Field>
      <ErrorLine message={error} />
      <FormButtons submit="Send for approval" busy={busy} onCancel={onCancel} />
    </form>
  );
}
