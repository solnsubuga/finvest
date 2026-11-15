import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { formatPrice, parsePrice } from "../products/bonds/api.ts";
import {
  BondSaleError,
  bondSaleErrorMessage,
  STATUS_LABELS,
  type BondSale,
  type BondSalesApi,
  type SalePrice,
} from "./bondSales.ts";
import { formatDay, formatExact, formatWhen } from "./format.ts";
import { FIELD, Field, FormButtons } from "./Orders.tsx";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; sales: BondSale[] };

/** The form open on a sale: its price, or why it goes back. */
type Open = { kind: "price" | "send_back"; id: string } | null;

interface BondSalesSectionProps {
  api: BondSalesApi;
  /** The Kampala business day, as a calendar date. */
  today: () => string;
}

const OPEN = new Set(["requested", "priced", "approved"]);

/** Clients' bonds the desk sells early: one person records the price, a different one approves it. */
export function BondSalesSection({ api, today }: BondSalesSectionProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const [open, setOpen] = useState<Open>(null);

  const fetchSales = useCallback(
    (): Promise<Load> =>
      api.list().then(
        (sales) => ({ state: "ready", sales }),
        (error: unknown) => ({ state: "failed", message: bondSaleErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchSales().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchSales]);

  async function run(on: string, command: () => Promise<BondSale>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      const sale = await command();
      setLoad((last) => (last.state === "ready" ? { state: "ready", sales: last.sales.map((s) => (s.id === sale.id ? sale : s)) } : last));
      setOpen(null);
      return true;
    } catch (error) {
      setFailed({ on, message: bondSaleErrorMessage(error) });
      if (error instanceof BondSaleError && (error.code === "conflict" || error.code === "not_found")) {
        const fresh = await fetchSales();
        if (fresh.state === "ready") setLoad(fresh);
      }
      return false;
    } finally {
      setBusy(null);
    }
  }

  const heading = (
    <h2 id="bond-sales-heading" className="m-0 text-xl font-extrabold">
      Bonds clients are selling
    </h2>
  );

  if (load.state === "loading") {
    return (
      <section aria-labelledby="bond-sales-heading" className="flex flex-col gap-3">
        {heading}
        <p role="status" className="m-0 text-md text-muted">
          Loading bond sales
        </p>
      </section>
    );
  }
  if (load.state === "failed") {
    return (
      <section aria-labelledby="bond-sales-heading" className="flex flex-col gap-3">
        {heading}
        <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h3 className="m-0 text-[17px] font-extrabold">Bond sales could not be loaded</h3>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchSales().then(setLoad)}>
            Try again
          </Button>
        </div>
      </section>
    );
  }

  const waiting = load.sales.filter((s) => OPEN.has(s.status));
  const done = load.sales.filter((s) => !OPEN.has(s.status));

  return (
    <section aria-labelledby="bond-sales-heading" className="flex flex-col gap-4">
      <div>
        {heading}
        <p className="m-0 max-w-[760px] text-md leading-normal text-muted">
          A client asked to sell their whole holding before it matures. Sell that face value on the secondary market, then
          record the price and the day it settled. A different person approves it, and the client is paid the price and the
          interest earned to that day, after tax.
        </p>
      </div>
      {waiting.length === 0 ? (
        <p className="m-0 max-w-[760px] rounded-xl border border-border bg-surface p-5 text-md leading-normal">
          No client is waiting to sell a bond. When one asks in the app, the sale shows here for the desk.
        </p>
      ) : (
        <ul aria-label="Sales waiting, newest first" className="m-0 flex max-w-[960px] list-none flex-col gap-3 p-0">
          {waiting.map((sale) => (
            <li key={sale.id}>
              <SaleCard
                sale={sale}
                busy={busy !== null}
                error={failed?.on === sale.id ? failed.message : null}
                open={open?.id === sale.id ? open.kind : null}
                today={today()}
                onOpen={(kind) => {
                  setFailed(null);
                  setOpen({ kind, id: sale.id });
                }}
                onClose={() => {
                  setOpen(null);
                  setFailed(null);
                }}
                onPrice={(price) => run(sale.id, () => api.price(sale.id, price))}
                onSendBack={(note) => run(sale.id, () => api.sendBack(sale.id, note))}
                onApprove={() => void run(sale.id, () => api.approve(sale.id))}
                onWithdraw={() => void run(sale.id, () => api.withdraw(sale.id))}
              />
            </li>
          ))}
        </ul>
      )}
      {done.length > 0 ? (
        <details className="max-w-[960px] rounded-xl border border-border bg-surface p-5">
          <summary className="cursor-pointer text-md font-bold">Sold, cancelled and closed ({done.length})</summary>
          <ul aria-label="Sales done, newest first" className="m-0 mt-3 flex list-none flex-col gap-3 p-0">
            {done.map((sale) => (
              <li key={sale.id} className="border-t border-divider pt-3 first:border-t-0 first:pt-0">
                <Summary sale={sale} />
                {sale.status === "sold" && sale.amount ? <Paid sale={sale} /> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function Summary({ sale }: { sale: BondSale }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-md font-bold">{sale.clientName}</span>
        <span className="text-sm">
          {formatExact(sale.face)} of {sale.name}
        </span>
      </div>
      <div className="text-sm font-semibold">{STATUS_LABELS[sale.status]}</div>
      <div className="text-xs text-muted">
        Asked {formatWhen(sale.requestedAt)}. Paid {formatExact(sale.cost)} for it.
      </div>
    </div>
  );
}

function Terms({ sale }: { sale: BondSale }) {
  if (sale.price === null || sale.soldOn === null) return null;
  return (
    <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      <dt className="text-muted">Price</dt>
      <dd className="m-0 font-semibold">{formatPrice(sale.price)} per 100</dd>
      <dt className="text-muted">Settled</dt>
      <dd className="m-0 font-semibold">{formatDay(sale.soldOn)}</dd>
      <dt className="text-muted">Desk reference</dt>
      <dd className="m-0 font-semibold break-words">{sale.reason}</dd>
      <dt className="text-muted">Priced by</dt>
      <dd className="m-0">
        {sale.pricedBy}
        {sale.pricedAt ? `, ${formatWhen(sale.pricedAt)}` : ""}
      </dd>
      {sale.decidedBy ? (
        <>
          <dt className="text-muted">Approved by</dt>
          <dd className="m-0">
            {sale.decidedBy}
            {sale.decidedAt ? `, ${formatWhen(sale.decidedAt)}` : ""}
          </dd>
        </>
      ) : null}
    </dl>
  );
}

function Paid({ sale }: { sale: BondSale }) {
  const row = (label: string, value: string) => (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="m-0 font-semibold tabular-nums">{value}</dd>
    </>
  );
  return (
    <div className="mt-2 flex flex-col gap-2">
      <Terms sale={sale} />
      <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
        {sale.clean ? row("Price paid", formatExact(sale.clean)) : null}
        {sale.accrued ? row("Accrued interest", formatExact(sale.accrued)) : null}
        {sale.tax ? row("Tax withheld", formatExact(sale.tax)) : null}
        {sale.amount ? row("Client paid", formatExact(sale.amount)) : null}
        {row(
          "Payout",
          sale.payoutStatus === "held" ? "Waiting for the client to name where it goes" : "Sent to the client's payout account",
        )}
      </dl>
    </div>
  );
}

interface SaleCardProps {
  sale: BondSale;
  busy: boolean;
  error: string | null;
  open: "price" | "send_back" | null;
  today: string;
  onOpen: (kind: "price" | "send_back") => void;
  onClose: () => void;
  onPrice: (price: SalePrice) => Promise<boolean>;
  onSendBack: (note: string) => Promise<boolean>;
  onApprove: () => void;
  onWithdraw: () => void;
}

function SaleCard(props: SaleCardProps) {
  const { sale, busy, error, open, today } = props;
  const what = `the sale of ${sale.clientName}'s ${sale.name}`;
  return (
    <article aria-label={`${sale.clientName}, ${sale.name}`} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <Summary sale={sale} />
        {open === null ? (
          <div className="flex shrink-0 flex-wrap gap-2">
            {sale.allowed.includes("price") ? (
              <Button disabled={busy} aria-label={`Record price for ${what}`} onClick={() => props.onOpen("price")}>
                Record price
              </Button>
            ) : null}
            {sale.allowed.includes("approve") ? (
              <Button disabled={busy} aria-label={`Approve ${what}`} onClick={props.onApprove}>
                Approve
              </Button>
            ) : null}
            {sale.allowed.includes("send_back") ? (
              <Button variant="secondary" disabled={busy} aria-label={`Send back ${what}`} onClick={() => props.onOpen("send_back")}>
                Send back
              </Button>
            ) : null}
            {sale.allowed.includes("withdraw") ? (
              <Button variant="secondary" disabled={busy} aria-label={`Withdraw price for ${what}`} onClick={props.onWithdraw}>
                Withdraw price
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      <Terms sale={sale} />
      {sale.status === "requested" && sale.note ? (
        <p className="m-0 rounded-md bg-surface-sunken px-3 py-2 text-sm">
          <span className="font-bold">Sent back: </span>
          {sale.note}
        </p>
      ) : null}
      {sale.status === "priced" && sale.allowed.includes("withdraw") ? (
        <p className="m-0 text-sm text-muted">You recorded this price. A different person approves it.</p>
      ) : null}
      {open === "price" ? (
        <PriceForm today={today} busy={busy} error={error} onClose={props.onClose} onSubmit={props.onPrice} />
      ) : null}
      {open === "send_back" ? (
        <NoteForm busy={busy} error={error} onClose={props.onClose} onSubmit={props.onSendBack} />
      ) : null}
      {error && open === null ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2 text-sm font-bold text-danger">
          {error}
        </p>
      ) : null}
    </article>
  );
}

function PriceForm(props: {
  today: string;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (price: SalePrice) => Promise<boolean>;
}) {
  const [price, setPrice] = useState("");
  const [soldOn, setSoldOn] = useState(props.today);
  const [reason, setReason] = useState("");
  const [problems, setProblems] = useState<{ price?: string; soldOn?: string; reason?: string }>({});

  function submit(event: FormEvent) {
    event.preventDefault();
    const parsed = parsePrice(price);
    const found: typeof problems = {};
    if (parsed === null) found.price = "Enter the price per 100 of face value, between 50 and 150, such as 97.25.";
    if (!soldOn) found.soldOn = "Enter the day the sale settled.";
    else if (soldOn > props.today) found.soldOn = "Record a sale once it has settled, on or before today.";
    if (!reason.trim()) found.reason = "Enter the desk's reference for the trade.";
    setProblems(found);
    if (parsed === null || Object.keys(found).length > 0) return;
    void props.onSubmit({ price: parsed, soldOn, reason: reason.trim() });
  }

  return (
    <form aria-label="Record the price" className="flex flex-col gap-4 border-t border-divider pt-3" onSubmit={submit} noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="sale-price" label="Price per 100" error={problems.price} hint="As the desk sold it, such as 97.25.">
          {(control) => <Input {...control} className={FIELD} inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />}
        </Field>
        <Field id="sale-day" label="Day it settled" error={problems.soldOn}>
          {(control) => (
            <Input {...control} className={FIELD} type="date" max={props.today} value={soldOn} onChange={(e) => setSoldOn(e.target.value)} />
          )}
        </Field>
      </div>
      <Field id="sale-reference" label="Desk reference" error={problems.reason} hint="The trade's reference, and who bought it.">
        {(control) => <Input {...control} className={FIELD} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />}
      </Field>
      {props.error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2 text-sm font-bold text-danger">
          {props.error}
        </p>
      ) : null}
      <FormButtons submit="Record price" busy={props.busy} onCancel={props.onClose} />
    </form>
  );
}

function NoteForm(props: { busy: boolean; error: string | null; onClose: () => void; onSubmit: (note: string) => Promise<boolean> }) {
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | undefined>(undefined);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!note.trim()) {
      setProblem("Say what is wrong, so the desk can put it right.");
      return;
    }
    setProblem(undefined);
    void props.onSubmit(note.trim());
  }

  return (
    <form aria-label="Send back" className="flex flex-col gap-4 border-t border-divider pt-3" onSubmit={submit} noValidate>
      <Field id="sale-note" label="Why it goes back" error={problem}>
        {(control) => <Input {...control} className={FIELD} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />}
      </Field>
      {props.error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2 text-sm font-bold text-danger">
          {props.error}
        </p>
      ) : null}
      <FormButtons submit="Send back" busy={props.busy} onCancel={props.onClose} />
    </form>
  );
}
