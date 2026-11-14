import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import type { CatalogueApi, Currency, InterestPaid, NewProductDraft, NewTermsDraft, Product, ProductKind } from "./api.ts";
import {
  amountInput,
  CURRENCY_LABELS,
  formatBps,
  formatDay,
  INTEREST_PAID_LABELS,
  KIND_LABELS,
  parseAmount,
  parsePercent,
  previewClientRate,
} from "./format.ts";
import { Reach } from "./Reach.tsx";

const CODE = /^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$/;
const SELECT = "h-[54px] w-full rounded-lg border border-border-strong bg-surface px-3 text-lg font-semibold disabled:bg-surface-sunken disabled:text-muted";
const TEXTAREA = "w-full rounded-lg border border-border-strong bg-surface p-3 text-md focus-visible:border-black aria-invalid:border-danger";
// Paying in and out exist in shillings only (CURRENCIES_THAT_CAN_OPEN in the backend).
const CURRENCIES_THAT_CAN_OPEN: readonly Currency[] = ["UGX"];

export function Field({ id, label, hint, problem, children }: { id: string; label: string; hint?: string | undefined; problem: string | null; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint ? <p className="m-0 text-xs text-muted">{hint}</p> : null}
      {problem ? <p className="m-0 text-sm font-bold text-danger">{problem}</p> : null}
    </div>
  );
}

/** The terms both forms share: term, minimum and when interest is paid. */
interface Terms {
  termText: string;
  openTerm: boolean;
  minimumText: string;
  interestPaid: InterestPaid;
}

function termProblem(terms: Terms): string | null {
  if (terms.openTerm) return terms.interestPaid === "at_maturity" ? "An open term has no maturity. Pay interest monthly." : null;
  const days = /^\d{1,4}$/.test(terms.termText.trim()) ? Number(terms.termText) : null;
  return days === null || days < 1 || days > 3650 ? "Type a term from 1 to 3,650 days." : null;
}

function termDays(terms: Terms): number | null {
  return terms.openTerm ? null : Number(terms.termText);
}

function TermFields({ terms, currency, checked, locked, onChange }: { terms: Terms; currency: Currency; checked: boolean; locked: boolean; onChange(next: Terms): void }) {
  const minimum = parseAmount(terms.minimumText, currency);
  return (
    <>
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 text-sm font-bold">Term</legend>
        <div className="flex flex-wrap gap-4 text-md">
          <label className="flex items-center gap-2">
            <input type="radio" name="term" checked={!terms.openTerm} onChange={() => onChange({ ...terms, openTerm: false })} />
            Fixed term
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="term"
              checked={terms.openTerm}
              onChange={() => onChange({ ...terms, openTerm: true, interestPaid: locked ? terms.interestPaid : "monthly" })}
            />
            Open term, until the client withdraws
          </label>
        </div>
      </fieldset>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field id="product-term" label="Term, days" problem={checked ? termProblem(terms) : null}>
          <Input
            id="product-term"
            inputMode="numeric"
            disabled={terms.openTerm}
            value={terms.openTerm ? "" : terms.termText}
            onChange={(event) => onChange({ ...terms, termText: event.target.value })}
          />
        </Field>
        <Field
          id="product-minimum"
          label={`Minimum, ${currency}`}
          problem={checked && minimum === null ? `Type the smallest amount${currency === "USD" ? ", such as 500.00" : ", such as 100,000"}.` : null}
        >
          <Input id="product-minimum" inputMode="decimal" value={terms.minimumText} onChange={(event) => onChange({ ...terms, minimumText: event.target.value })} />
        </Field>
      </div>
      <Field id="product-interest" label="Interest is paid" hint={locked ? "Clients have been told this, so it stays as it is." : undefined} problem={null}>
        <select
          id="product-interest"
          value={terms.interestPaid}
          disabled={locked}
          onChange={(event) => onChange({ ...terms, interestPaid: event.target.value as InterestPaid })}
          className={SELECT}
        >
          {(Object.keys(INTEREST_PAID_LABELS) as InterestPaid[]).map((value) => (
            <option key={value} value={value}>
              {INTEREST_PAID_LABELS[value]}
            </option>
          ))}
        </select>
      </Field>
    </>
  );
}

function Preview({ annualRateBps, taxBps, note }: { annualRateBps: number | null; taxBps: number | null; note: string }) {
  const ready = annualRateBps !== null && taxBps !== null && annualRateBps >= 1 && annualRateBps <= 5000;
  return (
    <div className="flex flex-col gap-1 rounded-lg bg-black p-4 text-white">
      <p className="m-0 text-xs font-bold text-on-black-muted">What a client sees</p>
      <p className="m-0 text-[22px] font-extrabold tracking-[-0.4px] text-lime tabular-nums">
        {ready ? `${previewClientRate(annualRateBps, taxBps)} a year, after tax` : "Type a rate and tax"}
      </p>
      <p className="m-0 text-sm leading-[1.45] text-on-black-muted">{note}</p>
    </div>
  );
}

interface NewProductFormProps {
  busy: boolean;
  error: string | null;
  onSubmit(draft: NewProductDraft): void;
}

/** A new product, sent for a second person to approve. It is created as a draft. */
export function NewProductForm({ busy, error, onSubmit }: NewProductFormProps) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ProductKind>("treasury_bill");
  const [currency, setCurrency] = useState<Currency>("UGX");
  const [terms, setTerms] = useState<Terms>({ termText: "", openTerm: false, minimumText: "", interestPaid: "at_maturity" });
  const [rateText, setRateText] = useState("");
  const [taxText, setTaxText] = useState("15");
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState(false);

  const annualRateBps = parsePercent(rateText);
  const taxBps = parsePercent(taxText);
  const minimum = parseAmount(terms.minimumText, currency);
  const problems = {
    code: CODE.test(code) && code.length <= 24 ? null : "Use capital letters, digits and hyphens, such as TREA-364.",
    name: name.trim() && name.trim().length <= 80 ? null : "Give the product the name clients will see.",
    term: termProblem(terms),
    minimum: minimum === null ? "minimum" : null,
    rate: annualRateBps === null || annualRateBps < 1 || annualRateBps > 5000 ? "Type a rate from 0.01 to 50, such as 15." : null,
    tax: taxBps === null ? "Type the tax as a percentage, such as 15." : null,
    reason: reason.trim() ? null : "Say why Finvest is adding this product.",
  };

  function submit(event: FormEvent) {
    event.preventDefault();
    setChecked(true);
    if (Object.values(problems).some(Boolean) || minimum === null || annualRateBps === null || taxBps === null) return;
    onSubmit({
      code,
      name: name.trim(),
      kind,
      currency,
      termDays: termDays(terms),
      minimumMinor: minimum,
      interestPaid: terms.interestPaid,
      annualRateBps,
      taxBps,
      reason: reason.trim(),
    });
  }

  return (
    <form noValidate aria-labelledby="new-product" onSubmit={submit} className="flex flex-wrap items-start gap-5">
      <section className="flex min-w-0 flex-[3_1_480px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
        <h2 id="new-product" className="m-0 text-[17px] font-extrabold">
          New product
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field id="product-code" label="Code" hint="Staff see it. It never changes." problem={checked ? problems.code : null}>
            <Input id="product-code" value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} aria-invalid={checked && !!problems.code} />
          </Field>
          <Field id="product-name" label="Name" problem={checked ? problems.name : null}>
            <Input id="product-name" value={name} onChange={(event) => setName(event.target.value)} aria-invalid={checked && !!problems.name} />
          </Field>
          <Field id="product-kind" label="Kind" problem={null}>
            <select id="product-kind" value={kind} onChange={(event) => setKind(event.target.value as ProductKind)} className={SELECT}>
              {(Object.keys(KIND_LABELS) as ProductKind[]).map((value) => (
                <option key={value} value={value}>
                  {KIND_LABELS[value]}
                </option>
              ))}
            </select>
          </Field>
          <Field
            id="product-currency"
            label="Currency"
            hint={CURRENCIES_THAT_CAN_OPEN.includes(currency) ? "It never changes." : "Finvest cannot take or pay out dollars yet, so it can be created but not opened."}
            problem={null}
          >
            <select id="product-currency" value={currency} onChange={(event) => setCurrency(event.target.value as Currency)} className={SELECT}>
              {(Object.keys(CURRENCY_LABELS) as Currency[]).map((value) => (
                <option key={value} value={value}>
                  {CURRENCY_LABELS[value]}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <TermFields terms={terms} currency={currency} checked={checked} locked={false} onChange={setTerms} />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field id="product-rate" label="Yearly rate before tax, %" problem={checked ? problems.rate : null}>
            <Input id="product-rate" inputMode="decimal" value={rateText} onChange={(event) => setRateText(event.target.value)} aria-invalid={checked && !!problems.rate} />
          </Field>
          <Field id="product-tax" label="Tax on interest, %" problem={checked ? problems.tax : null}>
            <Input id="product-tax" inputMode="decimal" value={taxText} onChange={(event) => setTaxText(event.target.value)} aria-invalid={checked && !!problems.tax} />
          </Field>
        </div>
        <Field id="product-reason" label="Reason, for the audit trail" problem={checked ? problems.reason : null}>
          <textarea id="product-reason" rows={3} value={reason} onChange={(event) => setReason(event.target.value)} aria-invalid={checked && !!problems.reason} className={TEXTAREA} />
        </Field>
      </section>
      <aside className="flex min-w-0 flex-[2_1_300px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
        <Preview
          annualRateBps={annualRateBps}
          taxBps={taxBps}
          note="Tax is never shown to clients. The rate starts the day a second person approves."
        />
        <p className="m-0 text-sm leading-[1.45] text-muted">
          A second person approves the new product. It is then a draft, which clients and the website never see, until a second change opens it.
        </p>
        {error ? (
          <p role="alert" className="m-0 text-sm font-bold text-danger">
            {error}
          </p>
        ) : null}
        <Button type="submit" disabled={busy}>
          Send for approval
        </Button>
        <Link to="/products" className="text-center text-sm font-bold underline underline-offset-2">
          Cancel
        </Link>
      </aside>
    </form>
  );
}

interface EditProductFormProps {
  api: CatalogueApi;
  product: Product;
  /** A change to this product already waits for a second person, so no other can be proposed. */
  waiting: boolean;
  busy: string | null;
  error: { on: string; message: string } | null;
  onTerms(draft: NewTermsDraft): void;
  onStatus(to: "live" | "paused", reason: string): void;
}

/** New terms for a product, and pausing or opening it, each sent for a second person to approve. */
export function EditProductForm({ api, product, waiting, busy, error, onTerms, onStatus }: EditProductFormProps) {
  const draft = product.status === "draft";
  const [name, setName] = useState(product.name);
  const [kind, setKind] = useState<ProductKind>(product.kind);
  const [terms, setTerms] = useState<Terms>({
    termText: product.termDays === null ? "" : String(product.termDays),
    openTerm: product.termDays === null,
    minimumText: amountInput(product.minimum),
    interestPaid: product.interestPaid,
  });
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState(false);

  const minimum = parseAmount(terms.minimumText, product.currency);
  const problems = {
    name: name.trim() && name.trim().length <= 80 ? null : "Give the product the name clients will see.",
    term: termProblem(terms),
    minimum: minimum === null ? "minimum" : null,
    reason: reason.trim() ? null : "Say why the terms are changing.",
  };

  function submit(event: FormEvent) {
    event.preventDefault();
    setChecked(true);
    if (Object.values(problems).some(Boolean) || minimum === null) return;
    onTerms({
      productCode: product.code,
      name: name.trim(),
      kind,
      termDays: termDays(terms),
      minimumMinor: minimum,
      interestPaid: terms.interestPaid,
      reason: reason.trim(),
    });
  }

  return (
    <div className="flex flex-wrap items-start gap-5">
      <form noValidate aria-labelledby="edit-product" onSubmit={submit} className="flex min-w-0 flex-[3_1_480px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
        <h2 id="edit-product" className="m-0 text-[17px] font-extrabold">
          Edit {product.name}
        </h2>
        <p className="m-0 text-sm text-muted">
          {product.code} · {product.currency}. The code and currency never change.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field id="product-name" label="Name" problem={checked ? problems.name : null}>
            <Input id="product-name" value={name} onChange={(event) => setName(event.target.value)} aria-invalid={checked && !!problems.name} />
          </Field>
          <Field id="product-kind" label="Kind" hint={draft ? undefined : "Clients have been told this, so it stays as it is."} problem={null}>
            <select id="product-kind" value={kind} disabled={!draft} onChange={(event) => setKind(event.target.value as ProductKind)} className={SELECT}>
              {(Object.keys(KIND_LABELS) as ProductKind[]).map((value) => (
                <option key={value} value={value}>
                  {KIND_LABELS[value]}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <TermFields terms={terms} currency={product.currency} checked={checked} locked={!draft} onChange={setTerms} />
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-surface-sunken p-4">
          <div className="flex flex-col gap-0.5">
            <p className="m-0 text-xs font-bold text-muted">Rate</p>
            <p className="m-0 text-md font-bold tabular-nums">
              {product.rate
                ? `${formatBps(product.rate.annualRateBps)} before tax, ${formatBps(product.rate.taxBps)} tax. Clients see ${product.rate.clientRatePercent}%.`
                : "No rate in force yet."}
            </p>
            {product.nextRate ? (
              <p className="m-0 text-xs text-muted">
                {product.nextRate.clientRatePercent}% from {formatDay(product.nextRate.effectiveFrom)}
              </p>
            ) : null}
          </div>
          <Link to="/rates" className="text-sm font-bold underline underline-offset-2">
            Change the rate on Rates
          </Link>
        </div>
        <Field id="product-reason" label="Reason, for the audit trail" problem={checked ? problems.reason : null}>
          <textarea id="product-reason" rows={3} value={reason} onChange={(event) => setReason(event.target.value)} aria-invalid={checked && !!problems.reason} className={TEXTAREA} />
        </Field>
        <p className="m-0 text-sm leading-[1.45] text-muted">New terms apply to new investments only. Running investments keep the terms they started with.</p>
        {error?.on === "terms" ? (
          <p role="alert" className="m-0 text-sm font-bold text-danger">
            {error.message}
          </p>
        ) : null}
        <Button type="submit" disabled={waiting || busy !== null}>
          Send for approval
        </Button>
      </form>
      <aside aria-label="Who this reaches and availability" className="flex min-w-0 flex-[2_1_300px] flex-col gap-4">
        <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-[17px] font-extrabold">Who this reaches</h2>
          <Reach api={api} productCode={product.code} change="terms" />
        </section>
        <StatusForm product={product} waiting={waiting} busy={busy === "status"} error={error?.on === "status" ? error.message : null} onSubmit={onStatus} />
      </aside>
    </div>
  );
}

function StatusForm({ product, waiting, busy, error, onSubmit }: { product: Product; waiting: boolean; busy: boolean; error: string | null; onSubmit(to: "live" | "paused", reason: string): void }) {
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState(false);
  const to = product.status === "live" ? "paused" : "live";
  const blocked =
    to === "live" && !CURRENCIES_THAT_CAN_OPEN.includes(product.currency)
      ? "Finvest cannot take payments or pay out in dollars yet, so this product stays paused. It can be opened once dollar payments exist."
      : to === "live" && !product.rate
        ? "It has no rate in force yet. Approve a rate on Rates before opening it."
        : null;
  const title = to === "paused" ? "Pause new investments" : product.status === "draft" ? "Open to clients" : "Open again";
  const summary =
    to === "paused"
      ? "Clients can no longer start investments in it. Running ones carry on to maturity. The website shows it as coming soon."
      : "Clients can start investments in it, and choose it for goals and automatic investing.";

  function submit(event: FormEvent) {
    event.preventDefault();
    setChecked(true);
    if (!reason.trim() || blocked) return;
    onSubmit(to, reason.trim());
  }

  return (
    <form noValidate aria-labelledby="product-status" onSubmit={submit} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <h2 id="product-status" className="m-0 text-[17px] font-extrabold">
        {title}
      </h2>
      <p className="m-0 text-sm leading-[1.45] text-muted">{summary}</p>
      {blocked ? (
        <p className="m-0 rounded-lg bg-warning-surface p-3 text-sm leading-[1.45] text-warning-text">{blocked}</p>
      ) : (
        <Field id="status-reason" label="Reason, for the audit trail" problem={checked && !reason.trim() ? "Say why." : null}>
          <textarea id="status-reason" rows={2} value={reason} onChange={(event) => setReason(event.target.value)} aria-invalid={checked && !reason.trim()} className={TEXTAREA} />
        </Field>
      )}
      {error ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}
      <Button type="submit" variant={to === "paused" ? "secondary" : "primary"} disabled={waiting || busy || blocked !== null}>
        {to === "paused" ? "Send pause for approval" : "Send opening for approval"}
      </Button>
    </form>
  );
}
