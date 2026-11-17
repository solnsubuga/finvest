import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { Label } from "@/components/ui/label.tsx";
import { cn } from "@/lib.ts";
import { formatBps, formatDay, formatExact, formatStamp, parsePercent } from "../format.ts";
import { Field } from "../ProductForm.tsx";
import { proposedBy } from "../proposer.ts";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { useNames } from "@/screens/review-queue/names.ts";
import {
  BondsError,
  bondsErrorMessage,
  clientCoupon,
  formatPrice,
  parsePrice,
  usualTaxBps,
  type Bond,
  type BondChange,
  type BondsApi,
  type BondStatus,
  type NewBondDraft,
} from "./api.ts";
import { DateInput } from "@/components/ui/date-input.tsx";

const STATUS_LABELS: Record<BondStatus, string> = { draft: "Draft", live: "Live", paused: "Paused" };
const STATUS_STYLES: Record<BondStatus, string> = {
  live: "bg-success-surface text-success",
  paused: "bg-warning-surface text-warning-text",
  draft: "bg-surface-sunken text-black",
};
const ACTION_LABELS: Record<BondChange["action"], string> = {
  create: "New bond",
  price: "New price",
  pause: "Pause",
  open: "Open to clients",
};
const TEXTAREA = "w-full rounded-lg border border-border-strong bg-surface p-3 text-md focus-visible:border-black aria-invalid:border-danger";

interface Book {
  bonds: readonly Bond[];
  waiting: readonly BondChange[];
}

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; book: Book };

interface BondsSectionProps {
  api: BondsApi;
  me: string;
  /** Names the colleague who proposed each change. */
  directory?: Directory;
  canChange: boolean;
}

/** Bond issues on the Products screen: each one's ISIN, coupon, tax, dates and price. */
export function BondsSection({ api, me, directory = emptyDirectory, canChange }: BondsSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [round, setRound] = useState(0);
  const { staff } = useNames(directory, [], load.state === "ready" ? load.book.waiting.map((change) => change.proposedBy) : []);

  const fetchBook = useCallback(async (): Promise<Load> => {
    try {
      const [bonds, waiting] = await Promise.all([api.listBonds(), api.listChanges("proposed")]);
      return { state: "ready", book: { bonds, waiting } };
    } catch (error) {
      return { state: "failed", message: bondsErrorMessage(error) };
    }
  }, [api]);

  useEffect(() => {
    let current = true;
    void fetchBook().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchBook]);

  async function run(on: string, command: () => Promise<unknown>): Promise<void> {
    setBusy(on);
    setFailed(null);
    try {
      await command();
      setLoad(await fetchBook());
      setRound((count) => count + 1);
    } catch (error) {
      setFailed({ on, message: bondsErrorMessage(error) });
      if (error instanceof BondsError && error.code === "conflict") {
        const fresh = await fetchBook();
        if (fresh.state === "ready") setLoad(fresh);
      }
    } finally {
      setBusy(null);
    }
  }

  const heading = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-col gap-1">
        <h2 id="bonds" className="m-0 text-xl font-extrabold">
          Treasury bonds
        </h2>
        <p className="m-0 max-w-[680px] text-md leading-normal text-muted">
          Each bond is a Bank of Uganda issue with its own ISIN, coupon and dates. Clients buy a face value at today&apos;s price and are paid the coupon every six
          months, after tax.
        </p>
      </div>
    </div>
  );

  if (load.state === "loading") {
    return (
      <section aria-labelledby="bonds" className="flex flex-col gap-4">
        {heading}
        <p role="status" className="m-0 text-md text-muted">
          Loading bonds
        </p>
      </section>
    );
  }
  if (load.state === "failed") {
    return (
      <section aria-labelledby="bonds" className="flex flex-col gap-4">
        {heading}
        <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-lg font-extrabold">Bonds could not be loaded</h3>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchBook().then(setLoad)}>
            Try again
          </Button>
        </div>
      </section>
    );
  }

  const { bonds, waiting } = load.book;
  const shown = bonds.find((b) => b.code === selected) ?? bonds[0];
  return (
    <section aria-labelledby="bonds" className="flex flex-col gap-4">
      {heading}
      {waiting.length > 0 ? (
        <div className="flex flex-col gap-3">
          <h3 className="m-0 text-[17px] font-extrabold">Bond changes waiting for approval</h3>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,340px),1fr))] items-start gap-4">
            {waiting.map((change) => (
              <BondChangeCard
                key={`${change.id}:${round}`}
                change={change}
                current={bonds.find((b) => b.code === change.code) ?? null}
                mine={change.proposedBy === me}
                proposerName={staff[change.proposedBy]}
                canDecide={canChange}
                busy={busy === change.id}
                error={failed?.on === change.id ? failed.message : null}
                onApprove={() => void run(change.id, () => api.approve(change.id))}
                onSendBack={(note) => void run(change.id, () => api.sendBack(change.id, note))}
                onWithdraw={() => void run(change.id, () => api.withdraw(change.id))}
              />
            ))}
          </div>
        </div>
      ) : null}
      {bonds.length === 0 ? (
        <div className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">No bonds yet</h3>
          <p className="m-0 text-md leading-normal text-muted">
            {canChange
              ? "Add a bond from Bank of Uganda's auction results with New product, type Treasury bond: its ISIN, coupon, dates and price. A second person approves it."
              : "Staff who can change products add bonds, with a second person approving."}
          </p>
        </div>
      ) : (
        <div className="flex flex-wrap items-start gap-5">
          <div className="min-w-0 flex-[3_1_520px] overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label="Bonds">
            <table className="w-full min-w-[680px] border-collapse text-md">
              <caption className="sr-only">Bonds</caption>
              <thead>
                <tr className="text-left text-xs text-muted">
                  {["Bond", "Coupon before tax", "Tax", "Clients see", "Matures", "Price per 100", "Status"].map((name) => (
                    <th key={name} scope="col" className="px-3 py-3.5 font-bold">
                      {name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {bonds.map((bond) => (
                  <tr key={bond.code} aria-selected={bond.code === shown?.code} className={cn("border-t border-divider tabular-nums", bond.code === shown?.code && "bg-surface-sunken")}>
                    <th scope="row" className="px-3 py-3 text-left font-bold">
                      <button
                        type="button"
                        className="inline-flex min-h-6 cursor-pointer items-center text-left font-bold underline-offset-2 hover:underline"
                        onClick={() => setSelected(bond.code)}
                      >
                        {bond.name}
                      </button>
                      <div className="text-xs font-medium text-muted">
                        {bond.code} · {bond.isin}
                      </div>
                    </th>
                    <td className="px-3 py-3">{formatBps(bond.couponBps)}</td>
                    <td className="px-3 py-3">{formatBps(bond.taxBps)}</td>
                    <td className="px-3 py-3 font-bold">{clientCoupon(bond.couponBps, bond.taxBps)}</td>
                    <td className="px-3 py-3">{formatDay(bond.maturityDate)}</td>
                    <td className="px-3 py-3">{bond.price === null ? "None yet" : formatPrice(bond.price)}</td>
                    <td className="px-3 py-3">
                      <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold whitespace-nowrap", STATUS_STYLES[bond.status])}>{STATUS_LABELS[bond.status]}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {shown ? (
            <BondPanel
              key={`${shown.code}:${round}`}
              bond={shown}
              canChange={canChange}
              waiting={waiting.some((c) => c.code === shown.code)}
              busy={busy === "price" || busy === "status"}
              error={failed?.on === "price" || failed?.on === "status" ? failed.message : null}
              onPrice={(price, reason) => void run("price", () => api.proposePrice(shown.code, price, reason))}
              onStatus={(to, reason) => void run("status", () => api.proposeStatus(shown.code, to, reason))}
            />
          ) : null}
        </div>
      )}
    </section>
  );
}

function Row({ name, value }: { name: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
      <dt className="text-muted">{name}</dt>
      <dd className="m-0 text-right font-bold tabular-nums">{value}</dd>
    </div>
  );
}

interface BondPanelProps {
  bond: Bond;
  canChange: boolean;
  waiting: boolean;
  busy: boolean;
  error: string | null;
  onPrice(price: number, reason: string): void;
  onStatus(to: "live" | "paused", reason: string): void;
}

/** One bond: what a client sees, and with `products.change` a new price, opening or pausing. */
function BondPanel({ bond, canChange, waiting, busy, error, onPrice, onStatus }: BondPanelProps) {
  const [priceText, setPriceText] = useState(bond.price === null ? "" : formatPrice(bond.price));
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState(false);
  const priceId = useId();
  const reasonId = useId();
  const price = parsePrice(priceText);
  const priceProblem = checked && price === null ? "Type a price between 50 and 150 per 100, to four decimal places." : null;
  const reasonProblem = checked && !reason.trim() ? "Say why. The reason goes in the audit trail." : null;
  const to = bond.status === "live" ? "paused" : "live";

  function submit(event: FormEvent) {
    event.preventDefault();
    setChecked(true);
    if (price !== null && reason.trim()) onPrice(price, reason.trim());
  }

  return (
    <section aria-labelledby="bond-view" className="flex min-w-0 flex-[2_1_300px] flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <h3 id="bond-view" className="m-0 text-[17px] font-extrabold">
        {bond.name}
      </h3>
      <dl className="m-0 flex flex-col gap-2.5">
        <Row name="ISIN" value={bond.isin} />
        <Row name="Issued" value={formatDay(bond.issueDate)} />
        <Row name="Matures" value={formatDay(bond.maturityDate)} />
        <Row name="Coupon" value={`${formatBps(bond.couponBps)} a year, paid every six months`} />
        <Row name="Tax on coupons" value={bond.taxBps === bond.usualTaxBps ? formatBps(bond.taxBps) : `${formatBps(bond.taxBps)} (usually ${formatBps(bond.usualTaxBps)})`} />
        <Row name="Smallest face value" value={`${formatExact(bond.minimumFace)}, in steps of ${formatExact(bond.faceStep)}`} />
        <Row name="Price set" value={bond.pricedAt ? formatStamp(bond.pricedAt) : "No price yet"} />
      </dl>
      <div className="flex flex-col gap-1 rounded-lg bg-surface-sunken p-4">
        <p className="m-0 text-xs font-bold text-muted">What a client sees</p>
        <p className="m-0 text-[22px] font-extrabold tracking-[-0.4px] tabular-nums">{clientCoupon(bond.couponBps, bond.taxBps)} a year</p>
        <p className="m-0 text-sm leading-[1.45] text-muted">
          The coupon after tax. Clients pay the price plus interest earned since the last coupon, and never see the tax.
          {bond.status === "paused" ? " The bond is paused, so clients can see it but not buy." : ""}
          {bond.status === "draft" ? " It is a draft, so clients and the website do not see it yet." : ""}
        </p>
      </div>
      {!canChange ? null : waiting ? (
        <p className="m-0 text-sm font-bold">A change to this bond is waiting for a second person. Nothing else can be proposed until it is decided.</p>
      ) : (
        <form className="flex flex-col gap-3 border-t border-divider pt-3" onSubmit={submit} noValidate>
          <Field id={priceId} label="Today's price per 100 of face value" hint="From the auction result or the dealing desk's quote. It is in force once a second person approves." problem={priceProblem}>
            <Input id={priceId} inputMode="decimal" value={priceText} aria-invalid={priceProblem !== null} onChange={(event) => setPriceText(event.target.value)} />
          </Field>
          <Field id={reasonId} label="Reason" problem={reasonProblem}>
            <textarea id={reasonId} rows={2} value={reason} aria-invalid={reasonProblem !== null} className={TEXTAREA} onChange={(event) => setReason(event.target.value)} />
          </Field>
          {error ? (
            <p role="alert" className="m-0 text-sm font-bold text-danger">
              {error}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy}>
              Propose new price
            </Button>
            {bond.status === "draft" || bond.status === "paused" || bond.status === "live" ? (
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => {
                  setChecked(true);
                  if (reason.trim()) onStatus(to, reason.trim());
                }}
              >
                {to === "live" ? "Propose opening to clients" : "Propose pausing"}
              </Button>
            ) : null}
          </div>
        </form>
      )}
    </section>
  );
}

interface BondChangeCardProps {
  change: BondChange;
  current: Bond | null;
  mine: boolean;
  proposerName?: string | null | undefined;
  canDecide: boolean;
  busy: boolean;
  error: string | null;
  onApprove(): void;
  onSendBack(note: string): void;
  onWithdraw(): void;
}

function BondChangeCard({ change, current, mine, proposerName, canDecide, busy, error, onApprove, onSendBack, onWithdraw }: BondChangeCardProps) {
  const [sendingBack, setSendingBack] = useState(false);
  const [note, setNote] = useState("");
  const noteId = useId();
  const terms = change.terms;
  const rows: [string, string][] = terms
    ? [
        ["Code", change.code],
        ["ISIN", terms.isin],
        ["Name", terms.name],
        ["Coupon", formatBps(terms.couponBps)],
        ["Tax on coupons", formatBps(terms.taxBps)],
        ["Clients see", clientCoupon(terms.couponBps, terms.taxBps)],
        ["Issued", formatDay(terms.issueDate)],
        ["Matures", formatDay(terms.maturityDate)],
        ["Smallest face value", formatExact(terms.minimumFace)],
        ["First price", change.price === null ? "" : formatPrice(change.price)],
      ]
    : [
        ["Bond", current ? `${current.name} (${change.code})` : change.code],
        ...(change.action === "price" && change.price !== null
          ? ([
              ["Price now", current?.price == null ? "None yet" : formatPrice(current.price)],
              ["New price", formatPrice(change.price)],
            ] as [string, string][])
          : ([["Status after", change.action === "open" ? "Live" : "Paused"]] as [string, string][])),
      ];
  return (
    <section aria-label={`${ACTION_LABELS[change.action]}: ${change.code}`} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex items-center justify-between gap-2">
        <h4 className="m-0 text-md font-extrabold">{ACTION_LABELS[change.action]}</h4>
        <span className="rounded-pill bg-warning-surface px-2.5 py-1 text-xs font-bold text-warning-text">Waiting</span>
      </div>
      <dl className="m-0 flex flex-col gap-2">
        {rows.map(([name, value]) => (
          <Row key={name} name={name} value={value} />
        ))}
        <Row name="Proposed" value={`${proposedBy(mine, proposerName)}, ${formatStamp(change.proposedAt)}`} />
      </dl>
      <p className="m-0 rounded-lg bg-surface-sunken p-3 text-sm leading-[1.45]">{change.reason}</p>
      {error ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}
      {!canDecide ? null : mine ? (
        <Button variant="secondary" disabled={busy} onClick={onWithdraw}>
          Withdraw this change
        </Button>
      ) : sendingBack ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (note.trim()) onSendBack(note.trim());
          }}
        >
          <Label htmlFor={noteId}>Why it is going back</Label>
          <textarea id={noteId} value={note} onChange={(event) => setNote(event.target.value)} rows={3} className={TEXTAREA} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !note.trim()}>
              Send back
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} onClick={onApprove}>
            Approve as second person
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => setSendingBack(true)}>
            Send back with a note
          </Button>
        </div>
      )}
    </section>
  );
}

const CODE = /^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$/;
const ISIN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

interface NewBondFormProps {
  busy: boolean;
  error: string | null;
  onSubmit(draft: NewBondDraft): void;
}

/** A new bond from Bank of Uganda's auction results. The tax fills in from the tenor. */
export function NewBondForm({ busy, error, onSubmit }: NewBondFormProps) {
  const [values, setValues] = useState({
    code: "",
    isin: "",
    name: "",
    coupon: "",
    issueDate: "",
    maturityDate: "",
    price: "",
    minimum: "100000",
    step: "100000",
    reason: "",
  });
  const [taxText, setTaxText] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const set = (key: keyof typeof values) => (event: { target: { value: string } }) => setValues((v) => ({ ...v, [key]: event.target.value }));
  const datesOk = DATE.test(values.issueDate) && DATE.test(values.maturityDate) && values.maturityDate > values.issueDate;
  const usual = datesOk ? usualTaxBps(values.issueDate, values.maturityDate) : null;
  const shownTax = taxText ?? (usual === null ? "" : formatBps(usual).replace("%", ""));
  const coupon = parsePercent(values.coupon);
  const tax = parsePercent(shownTax);
  const price = parsePrice(values.price);
  const minimum = /^\d{1,12}$/.test(values.minimum) ? Number(values.minimum) : null;
  const step = /^\d{1,12}$/.test(values.step) ? Number(values.step) : null;

  const problems = {
    code: CODE.test(values.code.trim()) ? null : "Upper-case letters, digits and hyphens, such as BOND-10Y-2037.",
    isin: ISIN.test(values.isin.trim()) ? null : "Twelve characters, as on the auction result, such as UG12K1405378.",
    name: values.name.trim() ? null : "Name the bond as clients will see it.",
    coupon: coupon !== null && coupon > 0 ? null : "Type the coupon from the auction result, such as 16 or 15.25.",
    dates: datesOk ? null : "Type both dates as YYYY-MM-DD. The bond matures after it is issued.",
    tax: tax !== null && tax < 10_000 ? null : "Type the tax on coupons, such as 20 or 10.",
    price: price !== null ? null : "Type a price between 50 and 150 per 100, to four decimal places.",
    face: minimum && step && minimum % step === 0 ? null : "The smallest face value is a whole number of steps, in shillings.",
    reason: values.reason.trim() ? null : "Say why. The reason goes in the audit trail.",
  };
  const shown = (key: keyof typeof problems) => (checked ? problems[key] : null);

  function submit(event: FormEvent) {
    event.preventDefault();
    setChecked(true);
    if (Object.values(problems).some((p) => p !== null) || coupon === null || tax === null || price === null || !minimum || !step) return;
    onSubmit({
      code: values.code.trim(),
      isin: values.isin.trim(),
      name: values.name.trim(),
      couponBps: coupon,
      taxBps: tax,
      issueDate: values.issueDate,
      maturityDate: values.maturityDate,
      minimumFaceMinor: minimum,
      faceStepMinor: step,
      price,
      reason: values.reason.trim(),
    });
  }

  return (
    <form className="flex max-w-[640px] flex-col gap-4 rounded-xl border border-border bg-surface p-5" onSubmit={submit} noValidate>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,260px),1fr))] gap-4">
        <Field id="bond-code" label="Code" hint="What staff call it, such as BOND-10Y-2037." problem={shown("code")}>
          <Input id="bond-code" value={values.code} onChange={set("code")} aria-invalid={shown("code") !== null} />
        </Field>
        <Field id="bond-isin" label="ISIN" problem={shown("isin")}>
          <Input id="bond-isin" value={values.isin} onChange={set("isin")} aria-invalid={shown("isin") !== null} />
        </Field>
      </div>
      <Field id="bond-name" label="Name" hint="As clients will see it, such as 10 Year Treasury Bond 2037." problem={shown("name")}>
        <Input id="bond-name" value={values.name} onChange={set("name")} aria-invalid={shown("name") !== null} />
      </Field>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,260px),1fr))] gap-4">
        <Field id="bond-issued" label="Issue date" hint="YYYY-MM-DD" problem={shown("dates")}>
          <DateInput id="bond-issued" value={values.issueDate} onChange={set("issueDate")} aria-invalid={shown("dates") !== null} />
        </Field>
        <Field id="bond-matures" label="Maturity date" hint="Coupons fall every six months back from it." problem={null}>
          <DateInput id="bond-matures" value={values.maturityDate} onChange={set("maturityDate")} aria-invalid={shown("dates") !== null} />
        </Field>
        <Field id="bond-coupon" label="Coupon, % a year before tax" problem={shown("coupon")}>
          <Input id="bond-coupon" inputMode="decimal" value={values.coupon} onChange={set("coupon")} aria-invalid={shown("coupon") !== null} />
        </Field>
        <Field
          id="bond-tax"
          label="Tax on coupons, %"
          hint={usual === null ? "Fills in from the dates: 20% under ten years, 10% at ten years or more." : `Bank of Uganda's rate for this length is ${formatBps(usual)}.`}
          problem={shown("tax")}
        >
          <Input id="bond-tax" inputMode="decimal" value={shownTax} onChange={(event) => setTaxText(event.target.value)} aria-invalid={shown("tax") !== null} />
        </Field>
        <Field id="bond-price" label="First price per 100 of face value" hint="Such as 98.5 or 101.125." problem={shown("price")}>
          <Input id="bond-price" inputMode="decimal" value={values.price} onChange={set("price")} aria-invalid={shown("price") !== null} />
        </Field>
        <Field id="bond-minimum" label="Smallest face value, UGX" problem={shown("face")}>
          <Input id="bond-minimum" inputMode="numeric" value={values.minimum} onChange={set("minimum")} aria-invalid={shown("face") !== null} />
        </Field>
        <Field id="bond-step" label="In steps of, UGX" problem={null}>
          <Input id="bond-step" inputMode="numeric" value={values.step} onChange={set("step")} aria-invalid={shown("face") !== null} />
        </Field>
      </div>
      {coupon !== null && tax !== null && tax < 10_000 ? (
        <p className="m-0 rounded-lg bg-surface-sunken p-3 text-sm leading-[1.45]">
          Clients will see a coupon of <strong>{clientCoupon(coupon, tax)} a year</strong>, after tax. They never see the tax.
        </p>
      ) : null}
      <Field id="bond-reason" label="Reason" problem={shown("reason")}>
        <textarea id="bond-reason" rows={3} value={values.reason} onChange={set("reason")} className={TEXTAREA} aria-invalid={shown("reason") !== null} />
      </Field>
      {error ? (
        <p role="alert" className="m-0 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          Send for approval
        </Button>
        <Link to="/products" className="inline-flex h-11 items-center justify-center rounded-pill border border-border-strong bg-surface px-4 text-sm font-bold text-ink hover:bg-surface-sunken">
          Cancel
        </Link>
      </div>
      <p className="m-0 text-sm leading-[1.45] text-muted">It is created as a draft once a second person approves. Opening it to clients is a change of its own.</p>
    </form>
  );
}
