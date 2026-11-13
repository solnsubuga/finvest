import { useId, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import type { Desk, Fill, NewOrder, Order, OrderKind, ProductPosition } from "./api.ts";
import { formatDay, formatExact, formatShort, formatTime, KIND_LABELS, parseAmount, STATUS_LABELS } from "./format.ts";
import { Row } from "./Position.tsx";

const MAX_NOTE = 500;
const FIELD = "h-12 w-full rounded-lg border border-border-strong bg-surface px-3.5 text-md font-semibold text-ink";

export type OrderCommand =
  | { action: "approve" | "withdraw" | "confirm_result" }
  | { action: "send_back" | "reject_result"; note: string }
  | { action: "record_result"; fill: Fill | null };

interface OrderCardProps {
  order: Order;
  position: ProductPosition | undefined;
  busy: boolean;
  /** Why the last action on this order did not go through. */
  error: string | null;
  today: string;
  onCommand: (command: OrderCommand) => void;
  /** The person closed a form without sending it, or after a try that failed. */
  onCancel: () => void;
}

/** One open order, what has happened to it, and what the signed-in person may do next. */
export function OrderCard({ order, position, busy, error, today, onCommand, onCancel }: OrderCardProps) {
  const [open, setOpen] = useState<"send_back" | "reject_result" | "record_result" | "confirm_result" | null>(null);
  const close = () => {
    setOpen(null);
    onCancel();
  };
  const can = (action: Order["allowed"][number]) => order.allowed.includes(action);
  const sells = order.kind === "secondary_sale";
  const waitsForOther =
    order.allowed.length === 0 || (order.status === "prepared" && !can("approve"))
      ? order.status === "prepared"
        ? "You prepared this order, so someone else has to approve it."
        : order.status === "result_recorded"
          ? "You recorded this result, so someone else has to confirm it."
          : null
      : null;

  return (
    <article
      aria-label={`${KIND_LABELS[order.kind]}, ${order.productName}`}
      className="flex flex-col gap-2.5 rounded-xl border border-border bg-surface p-5"
    >
      <div>
        <h3 className="m-0 text-[17px] font-extrabold">
          {KIND_LABELS[order.kind]}, {order.productName}
        </h3>
        <p className="m-0 text-xs font-bold text-muted">{STATUS_LABELS[order.status]}</p>
      </div>
      <dl className="m-0 flex flex-col gap-2.5">
        <Row name="Face value" value={formatExact(order.face)} />
        <Row name="Prepared by" value={`${order.preparedBy}, ${formatTime(order.preparedAt)}`} />
        {order.result ? (
          <>
            <Row name={sells ? "Face value sold" : "Face value bought"} value={formatExact(order.result.face)} />
            <Row name={sells ? "Cash received" : "Cash paid"} value={formatExact(order.result.amount)} />
            <Row name="Settled" value={formatDay(order.result.settledOn)} />
            <Row name="Matures" value={formatDay(order.result.maturesOn)} />
            <Row name="Custodian reference" value={order.result.custodianReference} />
            <Row name="Recorded by" value={order.resultRecordedBy ?? ""} />
          </>
        ) : null}
      </dl>
      {order.note ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3 py-2 text-sm leading-[1.45]">
          <strong>Result rejected:</strong> {order.note}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}

      {open === "record_result" ? (
        <ResultForm
          order={order}
          discountOnly={position?.discountOnly ?? false}
          today={today}
          busy={busy}
          onCancel={close}
          onSubmit={(fill) => onCommand({ action: "record_result", fill })}
        />
      ) : open === "confirm_result" ? (
        <div role="group" aria-label="Confirm the result" className="flex flex-col gap-2 border-t border-divider pt-3">
          <p className="m-0 text-md font-bold leading-normal">
            {order.result
              ? `Does the custodian's note show ${formatExact(order.result.face)} of face value for ${formatExact(order.result.amount)}?`
              : "Does the custodian's note match this result?"}
          </p>
          <p className="m-0 text-sm leading-[1.45] text-muted">
            This posts the {sells ? "sale" : "purchase"} to the ledger. A ledger entry is never edited afterwards.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy} onClick={() => onCommand({ action: "confirm_result" })}>
              Yes, confirm and post
            </Button>
            <Button variant="secondary" disabled={busy} onClick={close}>
              Cancel
            </Button>
          </div>
        </div>
      ) : open ? (
        <NoteForm
          label={open === "send_back" ? "Why are you sending it back?" : "What does not match the custodian's note?"}
          submit={open === "send_back" ? "Send back" : "Reject the result"}
          busy={busy}
          onCancel={close}
          onSubmit={(note) => onCommand({ action: open, note })}
        />
      ) : (
        <div className="flex flex-col gap-2">
          {can("approve") ? (
            <Button size="block" className="h-12 rounded-pill" disabled={busy} onClick={() => onCommand({ action: "approve" })}>
              Approve as second person
            </Button>
          ) : null}
          {can("confirm_result") ? (
            <Button size="block" className="h-12 rounded-pill" disabled={busy} onClick={() => setOpen("confirm_result")}>
              Confirm against the custodian's note
            </Button>
          ) : null}
          {can("record_result") ? (
            <Button size="block" className="h-12 rounded-pill" disabled={busy} onClick={() => setOpen("record_result")}>
              Record the result
            </Button>
          ) : null}
          {can("send_back") ? (
            <Button variant="secondary" size="block" className="h-12 rounded-pill" disabled={busy} onClick={() => setOpen("send_back")}>
              Send back
            </Button>
          ) : null}
          {can("reject_result") ? (
            <Button variant="secondary" size="block" className="h-12 rounded-pill" disabled={busy} onClick={() => setOpen("reject_result")}>
              Reject the result
            </Button>
          ) : null}
          {can("withdraw") ? (
            <Button
              variant="secondary"
              size="block"
              className="h-12 rounded-pill"
              disabled={busy}
              onClick={() => onCommand({ action: "withdraw" })}
            >
              Withdraw this order
            </Button>
          ) : null}
        </div>
      )}
      {waitsForOther ? <p className="m-0 text-sm leading-[1.45] text-muted">{waitsForOther}</p> : null}
      {can("confirm_result") ? (
        <p className="m-0 text-sm leading-[1.45] text-muted">
          Confirming posts the {sells ? "sale" : "purchase"} to the ledger
          {sells ? "." : " and allocates the bills to client investments, oldest payment first."}
        </p>
      ) : null}
    </article>
  );
}

function NoteForm(props: { label: string; submit: string; busy: boolean; onCancel: () => void; onSubmit: (note: string) => void }) {
  const id = useId();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!note.trim()) return setError("Write a short note so the other person knows what to change.");
    props.onSubmit(note.trim());
  }

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-2">
      <Label htmlFor={id}>{props.label}</Label>
      <textarea
        id={id}
        value={note}
        maxLength={MAX_NOTE}
        rows={3}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(event) => setNote(event.target.value)}
        className="w-full rounded-lg border border-border-strong bg-surface px-3.5 py-2.5 text-md text-ink aria-invalid:border-danger"
      />
      {error ? <FieldError id={`${id}-error`}>{error}</FieldError> : null}
      <FormButtons submit={props.submit} busy={props.busy} onCancel={props.onCancel} />
    </form>
  );
}

type ResultErrors = Partial<Record<"face" | "amount" | "settledOn" | "maturesOn" | "reference", string>>;

interface ResultFormProps {
  order: Order;
  discountOnly: boolean;
  today: string;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (fill: Fill | null) => void;
}

/** What the custodian's note says was dealt. A second person confirms it before anything is posted. */
function ResultForm({ order, discountOnly, today, busy, onCancel, onSubmit }: ResultFormProps) {
  const id = useId();
  const sells = order.kind === "secondary_sale";
  const [nothing, setNothing] = useState(false);
  const [face, setFace] = useState("");
  const [amount, setAmount] = useState("");
  const [settledOn, setSettledOn] = useState(today);
  const [maturesOn, setMaturesOn] = useState("");
  const [reference, setReference] = useState("");
  const [errors, setErrors] = useState<ResultErrors>({});
  const currency = order.face.currency;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (nothing) return onSubmit(null);
    const found: ResultErrors = {};
    const faceDealt = parseAmount(face, currency);
    const cash = parseAmount(amount, currency);
    if (!faceDealt) found.face = "Enter the face value as a whole number.";
    else if (faceDealt.minor > order.face.minor) found.face = `The order was for ${formatExact(order.face)}. A result cannot be larger.`;
    if (!cash) found.amount = "Enter the cash amount as a whole number.";
    else if (discountOnly && faceDealt && cash.minor > faceDealt.minor)
      found.amount = "A Treasury bill never costs or fetches more than its face value.";
    if (!settledOn) found.settledOn = "Enter the settlement date.";
    else if (settledOn > today) found.settledOn = "A result is recorded on or after its settlement date.";
    if (!maturesOn) found.maturesOn = "Enter the maturity date.";
    else if (settledOn && maturesOn <= settledOn) found.maturesOn = "The maturity date is after the settlement date.";
    if (!reference.trim()) found.reference = "Enter the reference on the custodian's note.";
    setErrors(found);
    if (Object.keys(found).length > 0 || !faceDealt || !cash) return;
    onSubmit({ face: faceDealt, amount: cash, settledOn, maturesOn, custodianReference: reference.trim() });
  }

  return (
    <form noValidate onSubmit={submit} aria-label="Record the result" className="flex flex-col gap-3 border-t border-divider pt-3">
      <label className="flex items-center gap-2.5 text-md font-bold">
        <input type="checkbox" className="size-5" checked={nothing} onChange={(event) => setNothing(event.target.checked)} />
        Nothing was dealt
      </label>
      {nothing ? (
        <p className="m-0 text-sm leading-[1.45] text-muted">The order ends as unsuccessful. No money moves.</p>
      ) : (
        <>
          <Field id={`${id}-face`} label={`Face value ${sells ? "sold" : "bought"} (${currency})`} error={errors.face}>
            {(control) => (
              <Input {...control} className={FIELD} inputMode="numeric" value={face} onChange={(event) => setFace(event.target.value)} />
            )}
          </Field>
          <Field id={`${id}-amount`} label={`Cash ${sells ? "received" : "paid"} (${currency})`} error={errors.amount}>
            {(control) => (
              <Input
                {...control}
                className={FIELD}
                inputMode="numeric"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
              />
            )}
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field id={`${id}-settled`} label="Settled on" error={errors.settledOn}>
              {(control) => (
                <Input
                  {...control}
                  className={FIELD}
                  type="date"
                  max={today}
                  value={settledOn}
                  onChange={(event) => setSettledOn(event.target.value)}
                />
              )}
            </Field>
            <Field id={`${id}-matures`} label="Matures on" error={errors.maturesOn}>
              {(control) => (
                <Input
                  {...control}
                  className={FIELD}
                  type="date"
                  value={maturesOn}
                  onChange={(event) => setMaturesOn(event.target.value)}
                />
              )}
            </Field>
          </div>
          <Field id={`${id}-reference`} label="Custodian reference" error={errors.reference}>
            {(control) => (
              <Input
                {...control}
                className={FIELD}
                maxLength={64}
                value={reference}
                onChange={(event) => setReference(event.target.value)}
              />
            )}
          </Field>
        </>
      )}
      <FormButtons submit={nothing ? "Record as unsuccessful" : "Record the result"} busy={busy} onCancel={onCancel} />
    </form>
  );
}

const KINDS: readonly OrderKind[] = ["auction_bid", "secondary_purchase", "secondary_sale"];

interface PrepareFormProps {
  desk: Desk;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: (order: NewOrder) => void;
}

/** A new order. It does nothing until a second person approves it. */
export function PrepareForm({ desk, busy, error, onCancel, onSubmit }: PrepareFormProps) {
  const id = useId();
  const [kind, setKind] = useState<OrderKind>("auction_bid");
  const [productCode, setProductCode] = useState(desk.positions[0]?.productCode ?? "");
  const [face, setFace] = useState("");
  const [faceError, setFaceError] = useState<string | null>(null);
  const position = desk.positions.find((candidate) => candidate.productCode === productCode);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!position) return;
    const amount = parseAmount(face, position.lotSize.currency);
    const problem = !amount
      ? "Enter the face value as a whole number."
      : amount.minor % position.lotSize.minor !== 0
        ? `Orders are in steps of ${formatExact(position.lotSize)}.`
        : kind === "secondary_sale" && amount.minor > position.faceHeld.minor
          ? `The desk holds ${formatExact(position.faceHeld)} of face value in this product.`
          : null;
    setFaceError(problem);
    if (!problem && amount) onSubmit({ kind, productCode, face: amount });
  }

  return (
    <form
      noValidate
      onSubmit={submit}
      aria-labelledby={`${id}-title`}
      className="flex flex-col gap-3 rounded-xl border-2 border-black bg-surface p-[19px]"
    >
      <h2 id={`${id}-title`} className="m-0 text-[17px] font-extrabold">
        Prepare an order
      </h2>
      <Field id={`${id}-kind`} label="What is it?">
        {(control) => (
          <select {...control} className={FIELD} value={kind} onChange={(event) => setKind(event.target.value as OrderKind)}>
            {KINDS.map((option) => (
              <option key={option} value={option}>
                {KIND_LABELS[option]}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field id={`${id}-product`} label="Product">
        {(control) => (
          <select {...control} className={FIELD} value={productCode} onChange={(event) => setProductCode(event.target.value)}>
            {desk.positions.map((option) => (
              <option key={option.productCode} value={option.productCode}>
                {option.productName}
              </option>
            ))}
          </select>
        )}
      </Field>
      {position ? (
        <Field
          id={`${id}-face`}
          label={`Face value (${position.lotSize.currency})`}
          error={faceError ?? undefined}
          hint={
            kind === "secondary_sale"
              ? `Face value held: ${formatExact(position.faceHeld)}`
              : `To buy: ${formatShort(position.toBuy)}. Already on order: ${formatShort(position.onOrder)}.`
          }
        >
          {(control) => (
            <Input {...control} className={FIELD} inputMode="numeric" value={face} onChange={(event) => setFace(event.target.value)} />
          )}
        </Field>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}
      <FormButtons submit="Send for approval" busy={busy} onCancel={onCancel} />
      <p className="m-0 text-sm leading-[1.45] text-muted">
        Nothing is bought or sold until a second person approves the order and it is placed with the dealer.
      </p>
    </form>
  );
}

interface ControlProps {
  id: string;
  "aria-invalid": true | undefined;
  "aria-describedby": string | undefined;
}

/** A label, one control, and the hint and error that describe it. */
function Field(props: {
  id: string;
  label: string;
  error?: string | undefined;
  hint?: string;
  children: (control: ControlProps) => ReactNode;
}) {
  const { id, label, error, hint, children } = props;
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children({ id, "aria-invalid": error ? true : undefined, "aria-describedby": describedBy })}
      {hint ? (
        <p id={`${id}-hint`} className="m-0 text-xs text-muted">
          {hint}
        </p>
      ) : null}
      {error ? <FieldError id={`${id}-error`}>{error}</FieldError> : null}
    </div>
  );
}

function FieldError({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} className="m-0 text-sm font-bold text-danger">
      {children}
    </p>
  );
}

function FormButtons({ submit, busy, onCancel }: { submit: string; busy: boolean; onCancel: () => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button type="submit" disabled={busy}>
        {submit}
      </Button>
      <Button variant="secondary" disabled={busy} onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}
