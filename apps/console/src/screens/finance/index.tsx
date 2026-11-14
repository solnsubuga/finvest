import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import {
  CURRENCIES,
  notConnectedTaxApi,
  TaxError,
  taxErrorMessage,
  type Currency,
  type MonthReport,
  type NewRemittance,
  type Remittance,
  type TaxApi,
  type TaxExport,
  type TaxMonth,
  type TaxScreen,
} from "./api.ts";
import { formatAmount, formatDay, formatMonth, parseAmount } from "./format.ts";
import { createHttpTaxApi } from "./http.ts";
import { createStubTaxApi } from "./stub.ts";

const SUMMARY = "Tax withheld from clients' interest, what is owed to URA, and paying it.";
const INPUT = "w-full rounded-lg border border-border-strong bg-surface px-3 py-2.5 text-md font-medium text-ink";
const NEW_PAYMENT = "new";
// Payments shown before "Show all": enough for the last few months.
const SHOWN = 4;

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; screen: TaxScreen };
type Detail = { state: "loading"; month: string } | { state: "failed"; month: string; message: string } | { state: "ready"; report: MonthReport };

const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

/** Hands the browser the exported file. */
export function saveFile({ file, filename }: TaxExport) {
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

interface FinanceScreenProps {
  api: TaxApi;
  /** `tax_remittances.change`: recording, approving and declining payments to URA. */
  canPay: boolean;
  newKey?: () => string;
  today?: () => string;
  save?: (exported: TaxExport) => void;
}

export function FinanceScreen({ api, canPay, newKey = () => crypto.randomUUID(), today = kampalaToday, save = saveFile }: FinanceScreenProps) {
  const [currency, setCurrency] = useState<Currency>("UGX");
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [detail, setDetail] = useState<Detail | null>(null);
  const [paying, setPaying] = useState<TaxMonth | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [failed, setFailed] = useState<{ on: string; message: string } | null>(null);
  const pending = useRef(new Map<string, { request: string; key: string }>());

  function keyFor(on: string, request: string): string {
    const waiting = pending.current.get(on);
    if (waiting?.request === request) return waiting.key;
    const key = newKey();
    pending.current.set(on, { request, key });
    return key;
  }

  const fetchScreen = useCallback(
    (): Promise<Load> =>
      api.getScreen(currency).then(
        (screen) => ({ state: "ready", screen }),
        (error: unknown) => ({ state: "failed", message: taxErrorMessage(error) }),
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

  function openMonth(month: string) {
    setDetail({ state: "loading", month });
    api.getMonth(currency, month).then(
      (report) => setDetail({ state: "ready", report }),
      (error: unknown) => setDetail({ state: "failed", month, message: taxErrorMessage(error) }),
    );
  }

  async function run(on: string, request: unknown, command: (key: string) => Promise<TaxScreen>): Promise<boolean> {
    setBusy(on);
    setFailed(null);
    try {
      setLoad({ state: "ready", screen: await command(keyFor(on, JSON.stringify(request))) });
      pending.current.delete(on);
      return true;
    } catch (error) {
      // Only an answer that never arrived leaves the command open. A refusal is an answer.
      if (error instanceof TaxError && error.code !== "unavailable") pending.current.delete(on);
      setFailed({ on, message: taxErrorMessage(error) });
      if (error instanceof TaxError && (error.code === "conflict" || error.code === "not_found")) {
        const fresh = await fetchScreen();
        if (fresh.state === "ready") setLoad(fresh);
      }
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function exportMonth(month: string) {
    setBusy(`export:${month}`);
    setFailed(null);
    try {
      save(await api.exportMonth(currency, month));
    } catch (error) {
      setFailed({ on: `export:${month}`, message: taxErrorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <PageHeader title="Finance" summary={SUMMARY} />
      <div role="group" aria-label="Currency" className="flex gap-1 rounded-pill border border-border bg-surface p-1">
        {CURRENCIES.map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={currency === option}
            onClick={() => {
              if (option === currency) return;
              setLoad({ state: "loading" });
              setDetail(null);
              setPaying(null);
              setFailed(null);
              setCurrency(option);
            }}
            className={cn(
              "cursor-pointer rounded-pill px-4 py-1.5 text-sm font-bold",
              currency === option ? "bg-black text-white" : "text-muted hover:text-ink",
            )}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  );

  if (load.state === "loading") {
    return (
      <>
        {header}
        <p role="status" className="m-0 text-md text-muted">
          Loading withholding tax
        </p>
      </>
    );
  }
  if (load.state === "failed") {
    return (
      <>
        {header}
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Withholding tax could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchScreen().then(setLoad)}>
            Try again
          </Button>
        </section>
      </>
    );
  }

  const { screen } = load;
  const thisMonth = today().slice(0, 7);
  const ended = screen.months.filter((m) => m.month < thisMonth);
  const owed = ended.reduce((total, m) => total + Math.max(0, m.outstanding.minor), 0);
  const nextDue = [...ended].reverse().find((m) => m.outstanding.minor > 0);
  const overdue = ended.filter((m) => m.overdue).length;
  const nothingYet = screen.months.every((m) => m.withheld.minor === 0) && screen.remittances.length === 0;

  return (
    <>
      {header}
      <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4">
        <Figure name="Held for URA now" value={formatAmount(screen.heldForUra)} note="Withheld and not yet paid, every month" />
        <Figure
          name="Owed for ended months"
          value={formatAmount({ minor: owed, currency })}
          note={overdue ? `${overdue} month${overdue === 1 ? "" : "s"} past the due date` : "None past the due date"}
          warn={overdue > 0}
        />
        <Figure
          name="Next payment due"
          value={nextDue ? formatDay(nextDue.dueOn) : "Nothing due"}
          note={nextDue ? `For ${formatMonth(nextDue.month)}` : "Every ended month is paid or waiting"}
          warn={nextDue?.overdue ?? false}
        />
      </dl>

      {nothingYet ? (
        <section className="flex max-w-[640px] flex-col gap-2 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">No tax withheld yet</h2>
          <p className="m-0 text-md leading-normal text-muted">
            Tax on {currency === "UGX" ? "shilling" : "dollar"} interest appears here month by month once clients start earning. Each month is paid to URA by the 15th
            of the month after.
          </p>
        </section>
      ) : (
        <>
          <MonthsTable
            months={screen.months}
            thisMonth={thisMonth}
            canPay={canPay}
            open={detail?.state === "ready" ? detail.report.month : (detail?.month ?? null)}
            onOpen={openMonth}
            onPay={(month) => {
              setPaying(month);
              setFailed(null);
            }}
          />
          <div className="flex flex-wrap items-start gap-5">
            <div className="flex min-w-0 flex-[3_1_480px] flex-col gap-4">
              {paying ? (
                <PaymentForm
                  key={paying.month}
                  month={paying}
                  today={today()}
                  busy={busy === NEW_PAYMENT}
                  error={failed?.on === NEW_PAYMENT ? failed.message : null}
                  onCancel={() => {
                    setPaying(null);
                    pending.current.delete(NEW_PAYMENT);
                    setFailed(null);
                  }}
                  onSubmit={(payment) =>
                    void run(NEW_PAYMENT, payment, (key) => api.record(key, payment)).then((done) => done && setPaying(null))
                  }
                />
              ) : null}
              {!paying && !detail ? (
                <p className="m-0 rounded-xl border border-dashed border-border-strong p-5 text-md leading-normal text-muted">
                  Open a month&rsquo;s clients for URA&rsquo;s return and its file. Record a payment once it has left the trust account.
                </p>
              ) : null}
              {detail ? (
                <MonthDetail
                  detail={detail}
                  exporting={busy === `export:${detail.state === "ready" ? detail.report.month : detail.month}`}
                  error={failed?.on.startsWith("export:") ? failed.message : null}
                  onExport={(month) => void exportMonth(month)}
                  onClose={() => setDetail(null)}
                />
              ) : null}
            </div>
            <aside aria-label="Payments to URA" className="flex min-w-0 flex-[2_1_320px] flex-col gap-4">
              <h2 className="m-0 text-[17px] font-extrabold">Payments to URA</h2>
              {screen.remittances.length === 0 ? (
                <p className="m-0 rounded-xl border border-border bg-surface p-5 text-md leading-normal text-muted">
                  No payments recorded yet. Record one after it has left the trust account, with URA&rsquo;s PRN and the bank&rsquo;s reference.
                </p>
              ) : null}
              {(showAll ? screen.remittances : screen.remittances.slice(0, SHOWN)).map((remittance) => (
                <PaymentCard
                  key={`${remittance.id}:${remittance.status}`}
                  remittance={remittance}
                  busy={busy === remittance.id}
                  error={failed?.on === remittance.id ? failed.message : null}
                  onApprove={() => void run(remittance.id, "approve", (key) => api.approve(key, remittance.id))}
                  onDecline={(reason) => void run(remittance.id, { decline: reason }, (key) => api.decline(key, remittance.id, reason))}
                />
              ))}
              {screen.remittances.length > SHOWN ? (
                <Button variant="secondary" className="self-start" onClick={() => setShowAll((all) => !all)}>
                  {showAll ? "Show the latest only" : `Show all ${screen.remittances.length} payments`}
                </Button>
              ) : null}
            </aside>
          </div>
        </>
      )}
    </>
  );
}

/** Brings a panel into view when it opens, which on a phone is below the list of months. */
function useScrolledTo<T extends HTMLElement = HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, []);
  return ref;
}

function Figure({ name, value, note, warn = false }: { name: string; value: string; note: string; warn?: boolean }) {
  return (
    <div className={cn("rounded-[18px] bg-surface p-4", warn ? "border-2 border-warning p-[15px]" : "border border-border")}>
      <dt className="text-sm text-muted">{name}</dt>
      <dd className="m-0 text-[22px] font-extrabold tracking-[-0.4px] tabular-nums">{value}</dd>
      <dd className={cn("m-0 text-xs font-bold", warn ? "text-warning-text" : "text-muted")}>{note}</dd>
    </div>
  );
}

function monthState(month: TaxMonth, thisMonth: string): { label: string; style: string } {
  if (month.month >= thisMonth) return { label: "This month", style: "bg-surface-sunken text-black" };
  if (month.withheld.minor === 0 && month.remitted.minor === 0) return { label: "Nothing withheld", style: "bg-surface-sunken text-muted" };
  if (month.outstanding.minor < 0) return { label: "Credit", style: "bg-surface-sunken text-black" };
  if (month.outstanding.minor === 0) {
    return month.waiting.minor > 0
      ? { label: "Waiting for approval", style: "bg-warning-surface text-warning-text" }
      : { label: "Paid", style: "bg-success-surface text-success" };
  }
  return month.overdue ? { label: "Overdue", style: "bg-danger-surface text-danger" } : { label: "To pay", style: "bg-warning-surface text-warning-text" };
}

interface MonthsTableProps {
  months: readonly TaxMonth[];
  thisMonth: string;
  canPay: boolean;
  open: string | null;
  onOpen: (month: string) => void;
  onPay: (month: TaxMonth) => void;
}

function MonthsTable({ months, thisMonth, canPay, open, onOpen, onPay }: MonthsTableProps) {
  return (
    <>
      <section aria-labelledby="months-phone" className="flex flex-col gap-3 md:hidden">
        <h2 id="months-phone" className="m-0 text-[17px] font-extrabold">
          Withholding tax by month
        </h2>
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {months.map((month) => {
            const state = monthState(month, thisMonth);
            const payable = canPay && month.month < thisMonth && month.outstanding.minor > 0;
            return (
              <li
                key={month.month}
                className={cn("flex flex-col gap-2.5 rounded-xl border border-border bg-surface p-4", open === month.month && "border-2 border-black p-[15px]")}
              >
                <div className="flex items-center justify-between gap-2">
                  <h3 className="m-0 text-md font-extrabold">{formatMonth(month.month)}</h3>
                  <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold whitespace-nowrap", state.style)}>{state.label}</span>
                </div>
                <dl className="m-0 grid grid-cols-2 gap-x-3 gap-y-1 text-sm tabular-nums">
                  <dt className="text-muted">Withheld</dt>
                  <dd className="m-0 text-right font-semibold">{formatAmount(month.withheld)}</dd>
                  <dt className="text-muted">Paid to URA</dt>
                  <dd className="m-0 text-right font-semibold">{formatAmount(month.remitted)}</dd>
                  {month.waiting.minor > 0 ? (
                    <>
                      <dt className="text-muted">Waiting for approval</dt>
                      <dd className="m-0 text-right font-semibold">{formatAmount(month.waiting)}</dd>
                    </>
                  ) : null}
                  <dt className="text-muted">Still to pay</dt>
                  <dd className="m-0 text-right font-extrabold">{formatAmount(month.outstanding)}</dd>
                  <dt className="text-muted">Due</dt>
                  <dd className="m-0 text-right font-semibold">{formatDay(month.dueOn)}</dd>
                </dl>
                <div className="flex gap-2">
                  <Button variant="secondary" className="h-9 px-3" onClick={() => onOpen(month.month)}>
                    Clients
                  </Button>
                  {payable ? (
                    <Button className="h-9 px-3" onClick={() => onPay(month)}>
                      Record payment
                    </Button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      </section>
      <div className="hidden overflow-x-auto rounded-xl border border-border bg-surface md:block">
        <table className="w-full min-w-[860px] border-collapse text-md">
          <caption className="px-4 pt-4 text-left text-[17px] font-extrabold">Withholding tax by month</caption>
          <thead>
            <tr className="text-left text-xs text-muted">
              {["Month", "Withheld", "Paid to URA", "Still to pay", "Due", "Status", ""].map((heading, index) => (
                <th key={heading || index} scope="col" className="px-3 py-3.5 font-bold">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {months.map((month) => {
              const state = monthState(month, thisMonth);
              const payable = canPay && month.month < thisMonth && month.outstanding.minor > 0;
              return (
                <tr key={month.month} className={cn("border-t border-divider tabular-nums", open === month.month && "bg-surface-sunken")}>
                  <th scope="row" className="px-3 py-3 text-left font-bold whitespace-nowrap">
                    {formatMonth(month.month)}
                  </th>
                  <td className="px-3 py-3 whitespace-nowrap">{formatAmount(month.withheld)}</td>
                  <td className="px-3 py-3 whitespace-nowrap">
                    {formatAmount(month.remitted)}
                    {month.waiting.minor > 0 ? <span className="block text-xs text-muted">{formatAmount(month.waiting)} waiting</span> : null}
                  </td>
                  <td className="px-3 py-3 font-bold whitespace-nowrap">{formatAmount(month.outstanding)}</td>
                  <td className="px-3 py-3 whitespace-nowrap">{formatDay(month.dueOn)}</td>
                  <td className="px-3 py-3">
                    <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold whitespace-nowrap", state.style)}>{state.label}</span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-2">
                      <Button variant="secondary" className="h-9 px-3" onClick={() => onOpen(month.month)} aria-label={`Clients for ${formatMonth(month.month)}`}>
                        Clients
                      </Button>
                      {payable ? (
                        <Button className="h-9 px-3" onClick={() => onPay(month)} aria-label={`Record a payment for ${formatMonth(month.month)}`}>
                          Record payment
                        </Button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

interface MonthDetailProps {
  detail: Detail;
  exporting: boolean;
  error: string | null;
  onExport: (month: string) => void;
  onClose: () => void;
}

function MonthDetail({ detail, exporting, error, onExport, onClose }: MonthDetailProps) {
  const shownAt = useScrolledTo();
  const month = detail.state === "ready" ? detail.report.month : detail.month;
  return (
    <section ref={shownAt} aria-labelledby="month-detail" className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="month-detail" className="m-0 text-[17px] font-extrabold">
            {formatMonth(month)}, client by client
          </h2>
          <p className="m-0 text-sm text-muted">For URA&rsquo;s withholding tax return. Opening this is recorded in the audit trail.</p>
        </div>
        <div className="flex gap-2">
          {detail.state === "ready" && detail.report.payees.length > 0 ? (
            <Button variant="secondary" className="h-9 px-3" disabled={exporting} onClick={() => onExport(month)}>
              {exporting ? "Preparing the file" : "Download for URA (CSV)"}
            </Button>
          ) : null}
          <Button variant="secondary" className="h-9 px-3" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3.5 py-3 text-md font-semibold text-danger">
          {error}
        </p>
      ) : null}
      {detail.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading the month
        </p>
      ) : detail.state === "failed" ? (
        <p role="alert" className="m-0 text-md text-danger">
          {detail.message}
        </p>
      ) : detail.report.payees.length === 0 ? (
        <p className="m-0 text-md text-muted">No client had tax withheld in this month.</p>
      ) : (
        <>
          {!detail.report.tinsShown ? (
            <p className="m-0 rounded-md bg-warning-surface px-3.5 py-3 text-sm font-semibold text-warning-text">
              This server cannot open tax numbers, so no TIN is shown. Ask the infrastructure team for the identity key.
            </p>
          ) : null}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse text-md">
              <caption className="sr-only">Clients with tax withheld in {formatMonth(month)}</caption>
              <thead>
                <tr className="text-left text-xs text-muted">
                  {["Client", "Account number", "TIN", "Interest before tax", "Tax withheld"].map((heading) => (
                    <th key={heading} scope="col" className="px-3 py-2.5 font-bold">
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {detail.report.payees.map((payee) => (
                  <tr key={payee.clientId} className="border-t border-divider tabular-nums">
                    <th scope="row" className="px-3 py-2.5 text-left font-bold">
                      {payee.name ?? payee.clientId}
                    </th>
                    <td className="px-3 py-2.5">{payee.accountNumber ?? "None"}</td>
                    <td className={cn("px-3 py-2.5", !payee.tin && "text-muted")}>{payee.tin ?? "Not given"}</td>
                    <td className="px-3 py-2.5">{formatAmount(payee.gross)}</td>
                    <td className="px-3 py-2.5 font-bold">{formatAmount(payee.tax)}</td>
                  </tr>
                ))}
                <tr className="border-t-2 border-border-strong tabular-nums">
                  <th scope="row" colSpan={3} className="px-3 py-2.5 text-left font-extrabold">
                    {detail.report.payees.length} client{detail.report.payees.length === 1 ? "" : "s"}
                  </th>
                  <td className="px-3 py-2.5 font-extrabold">{formatAmount(detail.report.gross)}</td>
                  <td className="px-3 py-2.5 font-extrabold">{formatAmount(detail.report.withheld)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

interface PaymentFormProps {
  month: TaxMonth;
  today: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: (payment: NewRemittance) => void;
}

function PaymentForm({ month, today, busy, error, onCancel, onSubmit }: PaymentFormProps) {
  const currency = month.outstanding.currency;
  const digits = currency === "USD" ? 2 : 0;
  const owed = month.outstanding.minor;
  const [amount, setAmount] = useState(
    digits ? `${Math.trunc(owed / 100)}.${String(owed % 100).padStart(2, "0")}` : String(owed),
  );
  const [ura, setUra] = useState("");
  const [bank, setBank] = useState("");
  const [paidOn, setPaidOn] = useState(today);
  const [note, setNote] = useState(`Withholding tax on interest, ${formatMonth(month.month)}`);
  const [problem, setProblem] = useState<string | null>(null);
  const shownAt = useScrolledTo<HTMLFormElement>();

  function submit(event: FormEvent) {
    event.preventDefault();
    const minor = parseAmount(amount, currency);
    if (minor === null) return setProblem("Enter the amount paid, above zero.");
    if (!ura.trim() || !bank.trim()) return setProblem("Enter URA's PRN and the bank's reference from the transfer.");
    if (!paidOn) return setProblem("Enter the day the money left the trust account.");
    if (!note.trim()) return setProblem("Add a note for the second person.");
    setProblem(null);
    onSubmit({
      currency,
      month: month.month,
      amountMinor: minor,
      uraReference: ura.trim(),
      bankReference: bank.trim(),
      paidOn,
      note: note.trim(),
    });
  }

  const shown = problem ?? error;
  return (
    <form ref={shownAt} onSubmit={submit} aria-labelledby="pay-title" className="flex flex-col gap-3 rounded-xl border-2 border-black bg-surface p-5">
      <div>
        <h2 id="pay-title" className="m-0 text-[17px] font-extrabold">
          Record a payment for {formatMonth(month.month)}
        </h2>
        <p className="m-0 text-sm leading-normal text-muted">
          Record it once the money has left the trust account. A second person checks it against URA&rsquo;s receipt and the bank statement, and their approval
          posts it. Still to pay: {formatAmount(month.outstanding)}.
        </p>
      </div>
      <Field label={`Amount paid (${currency})`}>
        <input className={INPUT} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
      </Field>
      <Field label="URA payment registration number (PRN)">
        <input className={INPUT} value={ura} maxLength={100} onChange={(e) => setUra(e.target.value)} />
      </Field>
      <Field label="Bank reference">
        <input className={INPUT} value={bank} maxLength={100} onChange={(e) => setBank(e.target.value)} />
      </Field>
      <Field label="Day it was paid">
        <input className={INPUT} type="date" value={paidOn} min={`${month.month}-01`} max={today} onChange={(e) => setPaidOn(e.target.value)} />
      </Field>
      <Field label="Note for the second person">
        <input className={INPUT} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
      </Field>
      {shown ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3.5 py-3 text-md font-semibold text-danger">
          {shown}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Recording" : "Record for approval"}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm font-bold">
      {label}
      {children}
    </label>
  );
}

const STATUS: Record<Remittance["status"], { label: string; style: string }> = {
  proposed: { label: "Waiting for a second person", style: "bg-warning-surface text-warning-text" },
  approved: { label: "Approved and posted", style: "bg-success-surface text-success" },
  declined: { label: "Declined", style: "bg-surface-sunken text-muted" },
};

interface PaymentCardProps {
  remittance: Remittance;
  busy: boolean;
  error: string | null;
  onApprove: () => void;
  onDecline: (reason: string) => void;
}

function PaymentCard({ remittance, busy, error, onApprove, onDecline }: PaymentCardProps) {
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const status = STATUS[remittance.status];
  const waiting = remittance.status === "proposed";
  return (
    <article
      aria-label={`Payment for ${formatMonth(remittance.month)}`}
      className={cn("flex flex-col gap-2.5 rounded-xl bg-surface p-4", waiting ? "border-2 border-warning p-[15px]" : "border border-border")}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="m-0 text-md font-extrabold">{formatMonth(remittance.month)}</h3>
          <p className="m-0 text-[22px] font-extrabold tabular-nums">{formatAmount(remittance.amount)}</p>
        </div>
        <span className={cn("rounded-pill px-2.5 py-1 text-xs font-bold whitespace-nowrap", status.style)}>{status.label}</span>
      </div>
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted">PRN</dt>
        <dd className="m-0 font-semibold break-all">{remittance.uraReference}</dd>
        <dt className="text-muted">Bank reference</dt>
        <dd className="m-0 font-semibold break-all">{remittance.bankReference}</dd>
        <dt className="text-muted">Paid on</dt>
        <dd className="m-0 font-semibold">{formatDay(remittance.paidOn)}</dd>
        <dt className="text-muted">Recorded by</dt>
        <dd className="m-0 font-semibold">{remittance.proposedByName ?? "A member of staff"}</dd>
        {remittance.decidedBy ? (
          <>
            <dt className="text-muted">{remittance.status === "declined" ? "Declined by" : "Approved by"}</dt>
            <dd className="m-0 font-semibold">{remittance.decidedByName ?? "A member of staff"}</dd>
          </>
        ) : null}
      </dl>
      <p className="m-0 text-sm text-muted">{remittance.note}</p>
      {remittance.decisionReason ? <p className="m-0 text-sm font-semibold">Reason: {remittance.decisionReason}</p> : null}
      {waiting && remittance.allowed.length === 0 ? (
        <p className="m-0 text-xs font-bold text-muted">Another member of staff approves this. It is not posted until they do.</p>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 rounded-md bg-danger-surface px-3 py-2.5 text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
      {remittance.allowed.length > 0 ? (
        declining ? (
          <div className="flex flex-col gap-2">
            <label className="flex flex-col gap-1.5 text-sm font-bold">
              Why is it declined?
              <input className={INPUT} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
            </label>
            <div className="flex gap-2">
              <Button className="h-9 px-3" disabled={busy || !reason.trim()} onClick={() => onDecline(reason.trim())}>
                Decline payment
              </Button>
              <Button variant="secondary" className="h-9 px-3" disabled={busy} onClick={() => setDeclining(false)}>
                Back
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2">
            {remittance.allowed.includes("approve") ? (
              <Button className="h-9 px-3" disabled={busy} onClick={onApprove}>
                {busy ? "Approving" : "Approve and post"}
              </Button>
            ) : null}
            {remittance.allowed.includes("decline") ? (
              <Button variant="secondary" className="h-9 px-3" disabled={busy} onClick={() => setDeclining(true)}>
                Decline
              </Button>
            ) : null}
          </div>
        )
      ) : null}
    </article>
  );
}

export default function FinancePage() {
  const { session } = useAuth();
  const [api] = useState<TaxApi>(() => {
    if (isStubAuth() && session) {
      // Under the stub, `?sample=empty` shows a new Finvest, and `?sample=offline` as if Finvest could not be reached.
      const sample = new URLSearchParams(window.location.search).get("sample");
      if (sample === "offline") return { ...notConnectedTaxApi, getScreen: () => Promise.reject(new TaxError("unavailable")) };
      return createStubTaxApi({ id: session.staffId, name: session.name }, { empty: sample === "empty" });
    }
    const base = apiBaseUrl();
    return base ? createHttpTaxApi(createHttp(base)) : notConnectedTaxApi;
  });
  return <FinanceScreen api={api} canPay={session ? can(session, "tax_remittances.change") : false} />;
}
