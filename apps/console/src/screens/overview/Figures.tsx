import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { figuresErrorMessage, type Amount, type Attention, type Currency, type Figures, type FiguresApi } from "./figures.ts";

const MINOR_DIGITS: Record<Currency, number> = { UGX: 0, USD: 2 };
const SYMBOL: Record<Currency, string> = { UGX: "UGX ", USD: "$" };
const GROUPED = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
const COMPACT = new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const WEEKDAY = new Intl.DateTimeFormat("en-GB", { weekday: "short", timeZone: "UTC" });
const LONG_DAY = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Africa/Kampala" });

/** The exact amount, from the integer: "UGX 4,820,350,000". */
export function exact(amount: Amount): string {
  const digits = MINOR_DIGITS[amount.currency];
  const unit = 10 ** digits;
  const whole = Math.trunc(Math.abs(amount.minor) / unit);
  const fraction = digits ? `.${String(Math.abs(amount.minor) % unit).padStart(digits, "0")}` : "";
  return `${amount.minor < 0 ? "-" : ""}${SYMBOL[amount.currency]}${GROUPED.format(whole)}${fraction}`;
}

/** A short label for a chart: "UGX 96M". Only for reading a bar, never for a decision. */
function short(amount: Amount): string {
  const whole = Math.trunc(amount.minor / 10 ** MINOR_DIGITS[amount.currency]);
  return `${SYMBOL[amount.currency]}${COMPACT.format(whole)}`;
}

const day = (iso: string) => DAY.format(new Date(`${iso}T00:00:00Z`));

/** A width for a bar, as a share of the largest, from integers. */
function share(part: number, whole: number): string {
  if (whole <= 0 || part <= 0) return "0%";
  return `${Math.max(2, Math.round((part * 100) / whole))}%`;
}

const NETWORK: Record<"mtn" | "airtel", string> = { mtn: "MTN", airtel: "Airtel" };

function sinceText(iso: string | null, now: Date): string {
  if (!iso) return "";
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} h` : `${Math.floor(hours / 24)} days`;
}

function plural(count: number, one: string, many: string): string {
  return `${GROUPED.format(count)} ${count === 1 ? one : many}`;
}

function attentionText(item: Attention, now: Date): { title: string; detail: string; to: string | null } {
  const waited = sinceText(item.since, now);
  switch (item.code) {
    case "float_low":
      return {
        title: `${NETWORK[item.subject === "airtel" ? "airtel" : "mtn"]} payout float is low`,
        detail: "Below the warning line. Top up the ioTec wallet to keep payouts instant.",
        to: "/money-movement",
      };
    case "payouts_waiting_for_float":
      return {
        title: `${plural(item.count, "payout waits", "payouts wait")} for float`,
        detail: waited ? `The first has waited ${waited}. They go once the wallet is topped up.` : "They go once the wallet is topped up.",
        to: "/money-movement",
      };
    case "interest_run_missing":
      return { title: "Today's interest run has not run", detail: "It runs after 02:00. Check the investments screen.", to: "/investments" };
    case "interest_run_behind":
      return { title: `${plural(item.count, "investment was", "investments were")} not paid interest`, detail: "The last run ended before paying them.", to: "/investments" };
    case "maturities_not_closed":
      return {
        title: `${plural(item.count, "maturity is", "maturities are")} not closed`,
        detail: item.subject ? `Due on ${day(item.subject)} or before.` : "Due and still open.",
        to: "/maturities",
      };
    case "books_not_balanced":
      return {
        title: "Books are not balanced with the providers",
        detail: item.subject ? `Last balanced on ${day(item.subject)}.` : "No day has been balanced yet.",
        to: "/reconciliation",
      };
    case "review_queue":
      return {
        title: `${plural(item.count, "item", "items")} in the review queue`,
        detail: waited ? `The oldest has waited ${waited}.` : "Waiting for a person.",
        to: "/review-queue",
      };
    case "approvals_for_you":
      return { title: `${plural(item.count, "change needs", "changes need")} your approval`, detail: "Listed below, oldest first.", to: null };
    case "day_close_to_approve":
      return {
        title: "A day's balancing waits for a second person",
        detail: item.subject ? `${day(item.subject)}, prepared and waiting for approval.` : "Prepared and waiting for approval.",
        to: "/reconciliation",
      };
  }
}

function Card({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  const id = `fig-${title.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`;
  return (
    <section aria-labelledby={id} className={cn("flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-surface p-5", className)}>
      <h2 id={id} className="m-0 text-[17px] font-extrabold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Tile({ label, value, detail, warn }: { label: string; value: string; detail: string; warn?: boolean }) {
  return (
    <div className={cn("min-w-0 rounded-lg bg-surface p-4", warn ? "border-2 border-warning-text" : "border border-border")}>
      <div className="text-sm text-muted">{label}</div>
      <div className="text-[22px] font-extrabold tracking-[-0.4px] break-words">{value}</div>
      <div className={cn("text-xs", warn ? "font-bold text-warning-text" : "text-muted")}>{detail}</div>
    </div>
  );
}

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; figures: Figures };

export function FiguresSection({ api, now }: { api: FiguresApi; now: () => Date }) {
  const [currency, setCurrency] = useState<Currency>("UGX");
  const [load, setLoad] = useState<Load>({ state: "loading" });

  const fetchFigures = useCallback(
    (chosen: Currency): Promise<Load> =>
      api.load(chosen).then(
        (figures) => ({ state: "ready", figures }),
        (error: unknown) => ({ state: "failed", message: figuresErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchFigures(currency).then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchFigures, currency]);

  const picker = (
    <div role="group" aria-label="Currency" className="flex gap-2">
      {(["UGX", "USD"] as const).map((code) => (
        <button
          key={code}
          type="button"
          aria-pressed={currency === code}
          onClick={() => {
            if (code === currency) return;
            setLoad({ state: "loading" });
            setCurrency(code);
          }}
          className={cn(
            "h-11 cursor-pointer rounded-pill px-4 text-sm",
            currency === code ? "bg-black font-bold text-white" : "border border-border-strong bg-surface font-semibold text-ink",
          )}
        >
          {code === "UGX" ? "Shillings" : "Dollars"}
        </button>
      ))}
    </div>
  );

  if (load.state === "loading") {
    return (
      <p role="status" className="m-0 text-md text-muted">
        Loading the figures
      </p>
    );
  }
  if (load.state === "failed") {
    return (
      <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
        <h2 className="m-0 text-[17px] font-extrabold">The figures could not be loaded</h2>
        <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
        <Button variant="secondary" onClick={() => void fetchFigures(currency).then(setLoad)}>
          Try again
        </Button>
      </div>
    );
  }

  const { figures } = load;
  const { money, floats, balancing, maturities, holdings } = figures;
  const at = now();
  const largestFlow = money ? Math.max(0, ...money.days.flatMap((d) => [d.moneyIn.minor, d.moneyOut.minor])) : 0;
  const netWeek = money ? money.days.reduce((sum, d) => sum + d.moneyIn.minor - d.moneyOut.minor, 0) : 0;
  const largestWeek = maturities ? Math.max(0, ...maturities.map((w) => w.principal.minor)) : 0;
  const sits = [
    ...(holdings ?? []).map((h) => ({ name: h.productName, amount: h.principal, detail: `${plural(h.investments, "investment", "investments")}, ${plural(h.clients, "client", "clients")}` })),
    ...(money
      ? [
          { name: "Paid in, waiting to start", amount: money.awaitingStart, detail: "Starts on the next business day" },
          { name: "In transit", amount: money.inTransit, detail: "Payouts handed to providers" },
        ]
      : []),
  ];
  const largestSit = Math.max(0, ...sits.map((s) => s.amount.minor));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="m-0 text-md text-muted">{LONG_DAY.format(new Date(`${figures.today}T00:00:00Z`))}, Kampala.</p>
        {picker}
      </div>

      {money ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4">
          <Tile label="Client money held" value={exact(money.clientMoneyHeld)} detail="Everything Finvest owes its clients" />
          <Tile label="In transit" value={exact(money.inTransit)} detail="Payouts handed to providers, not yet arrived" />
          <Tile
            label="Clients invested"
            value={GROUPED.format(money.clientsInvested)}
            detail={`${GROUPED.format(money.clientsVerified)} with an approved identity check`}
          />
          <Tile
            label="Interest added"
            value={money.interest ? exact(money.interest.net) : "No run yet"}
            detail={
              money.interest
                ? `${plural(money.interest.investments, "investment", "investments")}, ${day(money.interest.runDate)}${money.interest.finishedAt ? ` at ${TIME.format(new Date(money.interest.finishedAt))}` : ""}, after tax`
                : "The daily run has not run in this currency"
            }
            warn={!!money.interest && money.interest.behind > 0}
          />
        </div>
      ) : null}

      <div className="grid gap-5 [grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))]">
        {money ? (
          <Card title="Money in and out, last 7 days">
            <div className="flex gap-4 text-xs">
              <span className="flex items-center gap-1.5">
                <span aria-hidden="true" className="size-2.5 rounded-[3px] bg-black" />
                In
              </span>
              <span className="flex items-center gap-1.5">
                <span aria-hidden="true" className="size-2.5 rounded-[3px] bg-[#18D0DC]" />
                Out
              </span>
            </div>
            <ul aria-label="Money in and out by day" className="m-0 grid h-[180px] list-none grid-cols-7 items-end gap-2 p-0">
              {money.days.map((d) => (
                <li key={d.day} className="flex h-full flex-col items-center justify-end gap-1.5">
                  <span className="sr-only">
                    {day(d.day)}: in {exact(d.moneyIn)}, out {exact(d.moneyOut)}
                  </span>
                  <span aria-hidden="true" className="flex h-[150px] w-full items-end justify-center gap-1">
                    <span className="w-2/5 rounded-t-[5px] rounded-b-[2px] bg-black" style={{ height: share(d.moneyIn.minor, largestFlow) }} />
                    <span className="w-2/5 rounded-t-[5px] rounded-b-[2px] bg-[#18D0DC]" style={{ height: share(d.moneyOut.minor, largestFlow) }} />
                  </span>
                  <span aria-hidden="true" className="text-xs text-muted">
                    {WEEKDAY.format(new Date(`${d.day}T00:00:00Z`))}
                  </span>
                </li>
              ))}
            </ul>
            <p className="m-0 text-sm text-muted">
              {netWeek >= 0 ? "Net inflow" : "Net outflow"} this week:{" "}
              <strong className="text-ink">{exact({ minor: Math.abs(netWeek), currency: figures.currency })}</strong>. Largest day in or out:{" "}
              {short({ minor: largestFlow, currency: figures.currency })}.
            </p>
          </Card>
        ) : null}

        <Card title="Needs attention">
          {figures.attention.length === 0 ? (
            <p className="m-0 text-md text-muted">Nothing needs attention. Payouts, balancing and the interest run are on track.</p>
          ) : (
            <ul aria-label="Needs attention" className="m-0 flex list-none flex-col p-0">
              {figures.attention.map((item) => {
                const text = attentionText(item, at);
                return (
                  <li key={`${item.code}:${item.subject ?? ""}`} className="flex items-start gap-3 border-t border-divider py-2.5 first:border-t-0">
                    <span
                      aria-hidden="true"
                      className={cn("mt-1.5 size-2.5 shrink-0 rounded-full", item.tone === "warning" ? "bg-warning-text" : "bg-black")}
                    />
                    <span className="flex min-w-0 grow flex-col">
                      <span className="text-md font-bold">
                        {item.tone === "warning" ? <span className="sr-only">Warning: </span> : null}
                        {text.title}
                      </span>
                      <span className="text-sm text-muted">{text.detail}</span>
                    </span>
                    {text.to ? (
                      <Link to={text.to} className="shrink-0 text-sm font-bold text-ink">
                        Open<span className="sr-only">: {text.title}</span>
                      </Link>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        {floats ? (
          <Card title="Mobile money floats">
            <ul aria-label="Mobile money floats" className="m-0 flex list-none flex-col gap-3 p-0">
              {floats.map((f) => (
                <li key={f.network} className="flex flex-col gap-1">
                  <div className="flex justify-between gap-3 text-md">
                    <span className="font-bold">{NETWORK[f.network]}</span>
                    <span className={cn("font-bold", f.low ? "text-warning-text" : "")}>{exact(f.balance)}</span>
                  </div>
                  <div aria-hidden="true" className="flex h-2 rounded-full bg-border">
                    <span
                      className={cn("rounded-full", f.low ? "bg-warning-text" : "bg-black")}
                      style={{ width: share(f.balance.minor, Math.max(f.balance.minor, f.warningLine.minor * 3)) }}
                    />
                  </div>
                  <div className={cn("text-xs", f.low ? "font-bold text-warning-text" : "text-muted")}>
                    {f.low ? `Below the ${exact(f.warningLine)} warning line. Top up to keep payouts instant.` : `Above the ${exact(f.warningLine)} warning line.`}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        {balancing ? (
          <Card title="Daily balancing">
            <dl className="m-0">
              <Fact name="Last day balanced" value={balancing.lastClosed ? day(balancing.lastClosed) : "None yet"} />
              <Fact name="Waiting for a second person" value={balancing.waitingForApproval ? day(balancing.waitingForApproval) : "Nothing"} />
              <Fact name="Days behind" value={String(balancing.daysBehind)} warn={balancing.daysBehind > 1} />
            </dl>
            <Link to="/reconciliation" className="text-sm font-bold text-ink">
              Open reconciliation
            </Link>
          </Card>
        ) : null}

        {maturities ? (
          <Card title="Cash needed for maturities">
            <ul aria-label="Maturities by week" className="m-0 flex list-none flex-col gap-3 p-0">
              {maturities.map((week, n) => (
                <li key={week.firstDay} className="flex flex-col gap-1">
                  <div className="flex justify-between gap-3 text-sm">
                    <span>{n === 0 ? "This week" : `${day(week.firstDay)} to ${day(week.lastDay)}`}</span>
                    <span className="font-bold">{exact(week.principal)}</span>
                  </div>
                  <div aria-hidden="true" className="flex h-2 rounded-full bg-border">
                    <span className="rounded-full bg-black" style={{ width: share(week.principal.minor, largestWeek) }} />
                  </div>
                  <div className="text-xs text-muted">
                    {week.investments === 0
                      ? "Nothing matures"
                      : `${plural(week.investments, "investment", "investments")}. ${exact(week.payOutPrincipal)} set to pay out, the rest rolls over.`}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        {sits.length > 0 || holdings ? (
          <Card title="Where client money sits">
            {sits.every((s) => s.amount.minor === 0) ? (
              <p className="m-0 text-md text-muted">No client money is invested in this currency yet.</p>
            ) : (
              <ul aria-label="Where client money sits" className="m-0 flex list-none flex-col gap-3 p-0">
                {sits.map((s) => (
                  <li key={s.name} className="flex flex-col gap-1">
                    <div className="flex justify-between gap-3 text-sm">
                      <span>{s.name}</span>
                      <span className="font-bold">{exact(s.amount)}</span>
                    </div>
                    <div aria-hidden="true" className="flex h-2.5 rounded-full bg-border">
                      <span className="rounded-full border border-lime-border bg-lime" style={{ width: share(s.amount.minor, largestSit) }} />
                    </div>
                    <div className="text-xs text-muted">{s.detail}</div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        ) : null}
      </div>
    </div>
  );
}

function Fact({ name, value, warn }: { name: string; value: string; warn?: boolean }) {
  return (
    <div className="flex justify-between gap-3 border-b border-divider py-2.5 text-md">
      <dt className="text-muted">{name}</dt>
      <dd className={cn("m-0 font-bold", warn ? "text-warning-text" : "")}>{value}</dd>
    </div>
  );
}
