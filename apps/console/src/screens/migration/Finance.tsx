import { useId, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import type { Amounts, Currency, EnteredTotals, Overview, SignOff, TotalsInput } from "./api.ts";
import { Panel, PanelRow } from "./Checks.tsx";
import {
  difference,
  differenceSummary,
  formatAmount,
  formatDay,
  formatMoment,
  formatWithCurrency,
  lineLabel,
  parseAmount,
  parseCount,
} from "./labels.ts";

export interface FinanceActions {
  busy: boolean;
  onPropose: (key: string, reason: string) => void;
  onDecide: (signOffId: string, approve: boolean, reason?: string) => void;
}

const COUNTS = [
  ["clients", "Number of clients"],
  ["verifiedIdentities", "Verified identities"],
  ["runningInvestments", "Running investments"],
  ["pendingOrders", "Pending orders"],
] as const;
const MONEY = [
  ["invested", "Money invested"],
  ["interestBeforeTax", "Interest earned so far, before tax"],
] as const;
const CURRENCIES: readonly Currency[] = ["UGX", "USD"];

type CountName = (typeof COUNTS)[number][0];
type MoneyName = (typeof MONEY)[number][0];
type FieldName = CountName | `${MoneyName}.${Currency}`;

interface TotalsFormProps {
  busy: boolean;
  error: string | null;
  asOf: string;
  onEnter: (input: TotalsInput) => void;
  onCancel: () => void;
}

/** What Finvest 1.0's own report says it holds on the trial's "as of" day. */
export function TotalsForm({ busy, error, asOf, onEnter, onCancel }: TotalsFormProps) {
  const id = useId();
  const [values, setValues] = useState<Partial<Record<FieldName, string>>>({});
  const [errors, setErrors] = useState<Partial<Record<FieldName, string>>>({});

  function submit(event: FormEvent) {
    event.preventDefault();
    const found: Partial<Record<FieldName, string>> = {};
    const counts = {} as Record<CountName, string>;
    for (const [name] of COUNTS) {
      const parsed = parseCount(values[name] ?? "");
      if (parsed === null) found[name] = "Write a whole number, such as 1,912.";
      else counts[name] = parsed;
    }
    const money = { invested: {}, interestBeforeTax: {} } as Record<MoneyName, Amounts>;
    for (const [name] of MONEY) {
      for (const currency of CURRENCIES) {
        const field: FieldName = `${name}.${currency}`;
        const text = (values[field] ?? "").trim();
        if (text === "") continue;
        const parsed = parseAmount(text, currency);
        if (parsed === null) {
          found[field] = currency === "UGX" ? "Write whole shillings, such as 18,240,000,000." : "Write dollars with at most two decimals.";
        } else money[name][currency] = parsed;
      }
    }
    setErrors(found);
    if (Object.keys(found).length === 0) onEnter({ asOf, ...counts, ...money });
  }

  const field = (name: FieldName, label: string) => (
    <div key={name} className="flex flex-col gap-1.5">
      <Label htmlFor={`${id}-${name}`}>{label}</Label>
      <Input
        id={`${id}-${name}`}
        inputMode="decimal"
        autoComplete="off"
        className="tabular-nums"
        value={values[name] ?? ""}
        onChange={(event) => setValues((current) => ({ ...current, [name]: event.target.value }))}
        aria-invalid={errors[name] ? true : undefined}
        aria-describedby={errors[name] ? `${id}-${name}-error` : undefined}
      />
      {errors[name] ? (
        <p id={`${id}-${name}-error`} className="m-0 text-sm font-semibold text-danger">
          {errors[name]}
        </p>
      ) : null}
    </div>
  );

  return (
    <form
      noValidate
      aria-label="Enter the Finvest 1.0 totals"
      onSubmit={submit}
      className="flex flex-col gap-4 rounded-xl border-2 border-black bg-surface p-5"
    >
      <div>
        <h2 className="m-0 text-xl font-extrabold">Enter the Finvest 1.0 totals</h2>
        <p className="m-0 text-md leading-normal text-muted">
          Copy them from Finvest 1.0&apos;s own report for {formatDay(asOf)}, not from the spreadsheet. A second person
          checks them against the same report before they are compared with the trial. Leave a currency empty if Finvest
          1.0 holds none of it.
        </p>
      </div>
      <fieldset className="m-0 grid grid-cols-1 gap-4 border-0 p-0 sm:grid-cols-2 lg:grid-cols-4">
        <legend className="mb-2 text-md font-bold">Counts</legend>
        {COUNTS.map(([name, label]) => field(name, label))}
      </fieldset>
      {MONEY.map(([name, label]) => (
        <fieldset key={name} className="m-0 grid grid-cols-1 gap-4 border-0 p-0 sm:grid-cols-2">
          <legend className="mb-2 text-md font-bold">{label}</legend>
          {CURRENCIES.map((currency) => field(`${name}.${currency}`, currency === "UGX" ? "Shillings" : "Dollars"))}
        </fieldset>
      ))}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface p-3 text-md font-semibold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Saving the totals" : "Save for checking"}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function amountText(amounts: Amounts): string {
  const entries = CURRENCIES.filter((currency) => amounts[currency] !== undefined);
  return entries.length === 0 ? "None" : entries.map((currency) => formatWithCurrency(amounts[currency]!, currency)).join(", ");
}

interface TotalsPanelProps {
  totals: EnteredTotals | null;
  /** Enter or confirm: the backend guards both with the same permission. */
  mayEnter: boolean;
  busy: boolean;
  onOpenForm: () => void;
  onConfirm: (totalsId: string) => void;
}

/** Who entered the Finvest 1.0 totals, and whether a second person has checked them. */
export function TotalsPanel({ totals, mayEnter, busy, onOpenForm, onConfirm }: TotalsPanelProps) {
  // Entering confirmed totals again sets the check aside, so say so before the form opens.
  const [warning, setWarning] = useState(false);
  const confirmed = totals?.confirmedBy != null;
  return (
    <Panel title="Finvest 1.0 totals">
      {totals === null ? (
        <p className="m-0 border-t border-divider pt-2.5 text-md leading-normal text-muted">
          Not entered yet. Finance copies them from Finvest 1.0&apos;s own report, and a second person checks them.
        </p>
      ) : (
        <>
          {COUNTS.map(([name, label]) => (
            <PanelRow key={name} name={label} value={formatAmount(totals[name], null)} />
          ))}
          {MONEY.map(([name, label]) => (
            <PanelRow key={name} name={label} value={amountText(totals[name])} />
          ))}
          <p className="m-0 border-t border-divider pt-2.5 text-sm leading-normal text-muted">
            Entered by {totals.enteredBy} on {formatMoment(totals.enteredAt)}.{" "}
            {totals.confirmedBy
              ? `Checked by ${totals.confirmedBy === "You" ? "you" : totals.confirmedBy}.`
              : totals.mine
                ? "Someone else checks them against the report before they count."
                : "Check each figure against Finvest 1.0's report before confirming."}
          </p>
          {!totals.confirmedBy && !totals.mine && mayEnter ? (
            <Button onClick={() => onConfirm(totals.id)} disabled={busy} className="self-start">
              Confirm these totals
            </Button>
          ) : null}
        </>
      )}
      {mayEnter && warning ? (
        <div role="group" aria-label="Enter the totals again" className="flex flex-col gap-2 rounded-md bg-surface-sunken p-3 text-sm leading-snug">
          <p className="m-0">
            These totals are confirmed. Entering them again sets that check aside: nothing is compared until someone else confirms the
            new figures, and a sign-off on a total line counts again only if that line&apos;s figure stays the same.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => {
                setWarning(false);
                onOpenForm();
              }}
              disabled={busy}
              className="h-9 px-3"
            >
              Enter new totals
            </Button>
            <Button variant="secondary" onClick={() => setWarning(false)} disabled={busy} className="h-9 px-3">
              Keep these
            </Button>
          </div>
        </div>
      ) : mayEnter ? (
        <Button variant="secondary" onClick={confirmed ? () => setWarning(true) : onOpenForm} disabled={busy} className="self-start">
          {totals === null ? "Enter the totals" : "Enter them again"}
        </Button>
      ) : null}
    </Panel>
  );
}

interface Item {
  key: string;
  title: string;
  where: string | null;
  summary: string;
  signedOff: SignOff | null;
}

function lineSummary(old: string, next: string, currency: Currency | null): string {
  const gap = difference(old, next);
  const more = !gap.startsWith("-");
  const size = more ? gap : gap.slice(1);
  const amount = currency ? formatWithCurrency(size, currency) : formatAmount(size, null);
  const word = more ? "more" : currency ? "less" : "fewer";
  return `Finvest 1.0 shows ${amount} ${word}`;
}

/** Every total line and investment that differs, in the order the backend lists them. */
function items(overview: Overview): Item[] {
  const lines = overview.lines
    .filter((line) => line.old !== null && BigInt(line.old) !== BigInt(line.new))
    .map((line) => ({ key: line.key, title: lineLabel(line), where: null, summary: lineSummary(line.old!, line.new, line.currency), signedOff: line.signedOff }));
  const investments = overview.differences.map((item) => ({
    key: item.key,
    title: item.legacyInvestmentRef,
    where: item.row === null ? null : `row ${item.row}`,
    summary: differenceSummary(item),
    signedOff: item.signedOff,
  }));
  return [...lines, ...investments];
}

function ReasonForm({ label, submit, busy, onSubmit, onCancel }: { label: string; submit: string; busy: boolean; onSubmit: (reason: string) => void; onCancel: () => void }) {
  // The form is named by its button, so the reason box keeps the question as its own label.
  const id = useId();
  const [reason, setReason] = useState("");
  const [error, setError] = useState(false);
  return (
    <form
      noValidate
      aria-label={submit}
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const cleaned = reason.trim();
        setError(cleaned === "");
        if (cleaned) onSubmit(cleaned);
      }}
    >
      <Label htmlFor={id}>{label}</Label>
      <textarea
        id={id}
        rows={3}
        maxLength={500}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        className="w-full rounded-lg border border-border-strong bg-surface p-3 text-md text-ink"
      />
      {error ? (
        <p id={`${id}-error`} className="m-0 text-sm font-semibold text-danger">
          Say why, in a sentence someone else can check.
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy} className="h-9 px-3">
          {submit}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy} className="h-9 px-3">
          Cancel
        </Button>
      </div>
    </form>
  );
}

function SignOffRow({ item, maySignOff, actions }: { item: Item; maySignOff: boolean; actions: FinanceActions }) {
  const [open, setOpen] = useState<"propose" | "reject" | null>(null);
  const signOff = item.signedOff;
  return (
    <li className="flex flex-col gap-2 border-t border-divider py-2.5 text-md">
      <div className="flex flex-col">
        <span className="font-bold">
          {item.title} {item.where ? <span className="font-medium text-muted">{item.where}</span> : null}
        </span>
        <span className="text-sm text-muted">{item.summary}</span>
      </div>
      {signOff?.state === "rejected" ? (
        <div className="flex flex-col gap-2 rounded-md bg-surface-sunken p-3 text-sm leading-snug">
          <span>
            <span className="font-bold">Proposed by {signOff.mine ? "you" : signOff.proposedBy}:</span> {signOff.reason}
          </span>
          <span>
            <span className="font-bold text-danger">Rejected by {signOff.decidedBy}:</span> {signOff.decisionReason}
          </span>
          {maySignOff ? (
            open === "propose" ? (
              <ReasonForm
                label={`Why the difference on ${item.title} is right`}
                submit="Propose the sign-off"
                busy={actions.busy}
                onSubmit={(reason) => actions.onPropose(item.key, reason)}
                onCancel={() => setOpen(null)}
              />
            ) : (
              <Button variant="secondary" onClick={() => setOpen("propose")} disabled={actions.busy} className="h-9 self-start px-3">
                Propose again
              </Button>
            )
          ) : null}
        </div>
      ) : signOff ? (
        <div className="flex flex-col gap-2 rounded-md bg-surface-sunken p-3 text-sm leading-snug">
          <span>
            <span className="font-bold">Proposed by {signOff.mine ? "you" : signOff.proposedBy}:</span> {signOff.reason}
          </span>
          {signOff.mine ? <span className="text-muted">Someone else approves it.</span> : null}
          {!signOff.mine && maySignOff && open === null ? (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => actions.onDecide(signOff.id, true)} disabled={actions.busy} className="h-9 px-3">
                Approve
              </Button>
              <Button variant="secondary" onClick={() => setOpen("reject")} disabled={actions.busy} className="h-9 px-3">
                Reject
              </Button>
            </div>
          ) : null}
          {open === "reject" ? (
            <ReasonForm
              label={`Why reject the sign-off for ${item.title}`}
              submit="Reject"
              busy={actions.busy}
              onSubmit={(reason) => actions.onDecide(signOff.id, false, reason)}
              onCancel={() => setOpen(null)}
            />
          ) : null}
        </div>
      ) : maySignOff ? (
        open === "propose" ? (
          <ReasonForm
            label={`Why the difference on ${item.title} is right`}
            submit="Propose the sign-off"
            busy={actions.busy}
            onSubmit={(reason) => actions.onPropose(item.key, reason)}
            onCancel={() => setOpen(null)}
          />
        ) : (
          <Button variant="secondary" onClick={() => setOpen("propose")} disabled={actions.busy} className="h-9 self-start px-3">
            Sign off
          </Button>
        )
      ) : null}
    </li>
  );
}

/** What differs and still needs a sign-off. An approved one leaves the list; a rejected one stays with its reason. */
export function SignOffs({ overview, actions }: { overview: Overview; actions: FinanceActions }) {
  const all = items(overview);
  const open = all.filter((item) => item.signedOff?.state !== "approved");
  const waiting = open.filter((item) => item.signedOff?.state === "proposed").length;
  const maySignOff = overview.allowed.includes("sign_off");
  return (
    <Panel title="What differs">
      {all.length === 0 ? (
        <p className="m-0 text-sm leading-normal text-muted">
          Every total and every investment&apos;s interest and tax rate match what Finvest 2.0 works out.
        </p>
      ) : (
        <>
          {open.length > 0 ? (
            <ul aria-label="Differences to sign off" className="m-0 flex list-none flex-col p-0">
              {open.map((item) => (
                <SignOffRow key={item.key} item={item} maySignOff={maySignOff} actions={actions} />
              ))}
            </ul>
          ) : null}
          <PanelRow name="Waiting for a sign-off" value={open.length - waiting} />
          <PanelRow name="Waiting for approval" value={waiting} />
          <PanelRow name="Signed off by two people" value={all.length - open.length} />
        </>
      )}
      <p className="m-0 text-sm leading-normal text-muted">
        Each investment is opened with the interest Finvest 2.0 works out. A difference is never posted. One person proposes
        a sign-off with a reason and a different person approves it. If the figures change, it is asked for again.
      </p>
    </Panel>
  );
}
