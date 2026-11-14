import { useCallback, useEffect, useRef, useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { isStubAuth } from "@/auth/index.ts";
import { emptyDirectory, type ClientCard, type Directory } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import { formatBps, formatDay, formatExact, formatStamp } from "../products/format.ts";
import {
  investmentsErrorMessage,
  notConnectedInvestmentsApi,
  type AtMaturity,
  type InterestRun,
  type Investment,
  type InvestmentStatus,
  type InvestmentsApi,
} from "./api.ts";
import { CorrectionsPanel } from "./corrections/index.tsx";
import { createHttpInvestmentsApi } from "./http.ts";
import { createStubInvestmentsApi } from "./stub.ts";

const SUMMARY = "Client investments, what they have earned so far, and the daily interest run.";

const TABS: readonly { status: InvestmentStatus; label: string; empty: string }[] = [
  { status: "active", label: "Running", empty: "No investments are running." },
  { status: "awaiting_payment", label: "Awaiting payment", empty: "Nobody is waiting to pay for an investment." },
  { status: "matured", label: "Matured", empty: "No investment has matured yet." },
  { status: "withdrawn", label: "Withdrawn early", empty: "No investment has been withdrawn early." },
  { status: "unwound", label: "Undone", empty: "No investment has been undone." },
  { status: "cancelled", label: "Cancelled", empty: "No investment has been cancelled." },
];

const AT_MATURITY_LABELS: Record<AtMaturity, string> = { roll_over: "Roll over", pay_out: "Pay out" };

const REFUSAL_LABELS: Record<string, string> = {
  no_destination: "Waiting for the client to choose where to be paid",
  product_not_open: "Could not roll over: the product is not open",
};

type Load =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; investments: readonly Investment[]; runs: readonly InterestRun[] };

interface InvestmentsScreenProps {
  api: InvestmentsApi;
  /** Names each client. Without it, a client shows as their ID. */
  directory?: Directory;
  /** The Kampala business day, as a calendar date. */
  today?: () => string;
}

const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

function addDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function InvestmentsScreen({ api, directory = emptyDirectory, today = kampalaToday }: InvestmentsScreenProps) {
  const [tab, setTab] = useState<InvestmentStatus>("active");
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [cards, setCards] = useState<Record<string, ClientCard | null>>({});
  const asked = useRef(new Set<string>());

  // Every client asked for in the same moment goes in one request (directory/http.ts batches them).
  const lookUp = useCallback(
    (clientIds: readonly string[]) => {
      for (const clientId of new Set(clientIds)) {
        if (asked.current.has(clientId)) continue;
        asked.current.add(clientId);
        void directory.client(clientId).then(
          (card) => setCards((known) => ({ ...known, [clientId]: card })),
          () => setCards((known) => ({ ...known, [clientId]: null })),
        );
      }
    },
    [directory],
  );

  const fetchAll = useCallback(async (): Promise<Load> => {
    try {
      const [investments, runs] = await Promise.all([api.listInvestments(), api.interestRuns()]);
      lookUp(investments.map((investment) => investment.clientId));
      return { state: "ready", investments, runs };
    } catch (error) {
      return { state: "failed", message: investmentsErrorMessage(error) };
    }
  }, [api, lookUp]);

  useEffect(() => {
    let current = true;
    void fetchAll().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchAll]);

  if (load.state === "loading") {
    return (
      <>
        <PageHeader title="Investments" summary={SUMMARY} />
        <p role="status" className="m-0 text-md text-muted">
          Loading investments
        </p>
      </>
    );
  }
  if (load.state === "failed") {
    return (
      <>
        <PageHeader title="Investments" summary={SUMMARY} />
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Investments could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchAll().then(setLoad)}>
            Try again
          </Button>
        </section>
      </>
    );
  }

  const day = today();
  const weekEnd = addDays(day, 7);
  const shown = load.investments.filter((investment) => investment.status === tab);
  const maturing = load.investments
    .filter((i) => i.status === "active" && i.maturityDate !== null && i.maturityDate >= day && i.maturityDate <= weekEnd)
    .sort((a, b) => (a.maturityDate ?? "").localeCompare(b.maturityDate ?? ""));
  const current = TABS.find((t) => t.status === tab) ?? TABS[0];

  return (
    <>
      <PageHeader title="Investments" summary={SUMMARY} />
      <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] items-start gap-4">
        <LatestRun runs={load.runs} today={day} />
        <Maturing investments={maturing} />
      </div>
      <div className="flex min-w-0 flex-col gap-3">
        <div role="tablist" aria-label="Investments by status" className="flex flex-wrap gap-2">
          {TABS.map((t) => {
            const count = load.investments.filter((i) => i.status === t.status).length;
            return (
              <button
                key={t.status}
                type="button"
                role="tab"
                aria-selected={t.status === tab}
                onClick={() => setTab(t.status)}
                className={cn(
                  "h-10 cursor-pointer rounded-pill px-4 text-sm font-bold",
                  t.status === tab ? "bg-black text-white" : "border border-border-strong bg-surface text-ink hover:bg-surface-sunken",
                )}
              >
                {t.label} <span className="tabular-nums opacity-70">{count}</span>
              </button>
            );
          })}
        </div>
        {shown.length === 0 ? (
          <section role="tabpanel" className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
            <h2 className="m-0 text-[17px] font-extrabold">{current?.empty}</h2>
            <p className="m-0 text-md leading-normal text-muted">Investments appear here once a client reviews and confirms one in the app.</p>
          </section>
        ) : (
          <InvestmentTable investments={shown} status={tab} cards={cards} />
        )}
      </div>
      <RunHistory runs={load.runs} />
    </>
  );
}

interface InvestmentTableProps {
  investments: readonly Investment[];
  status: InvestmentStatus;
  /** Each client's card once it is read. Null when it cannot be. */
  cards: Readonly<Record<string, ClientCard | null>>;
}

function InvestmentTable({ investments, status, cards }: InvestmentTableProps) {
  const last = status === "awaiting_payment" ? "Pay by" : status === "matured" ? "What happened" : "At maturity";
  return (
    <div role="tabpanel" className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[720px] border-collapse text-md">
        <caption className="sr-only">Investments</caption>
        <thead>
          <tr className="text-left text-xs text-muted">
            {["Client", "Product", "Invested", "Interest so far", "Matures", last].map((heading) => (
              <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {investments.map((investment) => (
            <tr key={investment.id} className="border-t border-divider align-top tabular-nums">
              <th scope="row" className="px-3 py-3 text-left font-normal">
                <ClientName clientId={investment.clientId} card={cards[investment.clientId]} />
              </th>
              <td className="px-3 py-3">
                <div className="font-bold whitespace-nowrap">{investment.productName}</div>
                <div className="text-xs whitespace-nowrap text-muted">
                  {formatBps(investment.annualRateBps)} before {formatBps(investment.taxBps)} tax
                </div>
              </td>
              <td className="px-3 py-3 whitespace-nowrap">{formatExact(investment.amount)}</td>
              <td className="px-3 py-3 whitespace-nowrap">
                <div className="font-bold">{formatExact(investment.interestNet)}</div>
                <div className="text-xs text-muted">
                  {formatExact(investment.interestGross)} less {formatExact(investment.tax)} tax
                  {investment.accruedThrough ? `, to ${formatDay(investment.accruedThrough)}` : ""}
                </div>
              </td>
              <td className="px-3 py-3 whitespace-nowrap">
                {investment.maturityDate ? formatDay(investment.maturityDate) : investment.termDays === null ? "Open term" : "Once paid"}
              </td>
              <td className="px-3 py-3">
                <LastColumn investment={investment} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LastColumn({ investment }: { investment: Investment }) {
  if (investment.status === "awaiting_payment") return <span className="whitespace-nowrap">{formatStamp(investment.payBy)}</span>;
  if (investment.status === "cancelled") return <span className="text-muted">Not paid in time</span>;
  // The early withdrawal's payout is followed on Money Movement: this view does not carry it.
  if (investment.status === "withdrawn") return <span>Withdrawn early</span>;
  if (investment.status === "unwound") return <span className="text-muted">Undone, the money is waiting to start or to be refunded</span>;
  if (investment.status === "matured") {
    if (investment.rolledIntoId) return <span>Rolled over into a new term</span>;
    if (investment.payoutRefusal) {
      return <span className="font-bold text-warning-text">{REFUSAL_LABELS[investment.payoutRefusal] ?? `Held in payouts: ${investment.payoutRefusal}`}</span>;
    }
    // A payout ID means the payout was created, not that the money has arrived.
    return <span>{investment.payoutId ? "Payout created" : "Being paid out"}</span>;
  }
  return <span>{investment.atMaturity ? AT_MATURITY_LABELS[investment.atMaturity] : "Paid monthly"}</span>;
}

function LatestRun({ runs, today }: { runs: readonly InterestRun[]; today: string }) {
  const latestDay = runs[0]?.runDate;
  const latest = runs.filter((run) => run.runDate === latestDay);
  const behind = latest.reduce((count, run) => count + run.behind, 0);
  const late = latestDay !== undefined && latestDay < today;
  return (
    <section
      aria-labelledby="latest-run"
      className={cn("flex flex-col gap-3 rounded-xl bg-surface p-5", behind > 0 || late ? "border-2 border-warning p-[19px]" : "border border-border")}
    >
      <h2 id="latest-run" className="m-0 text-[17px] font-extrabold">
        Daily interest run
      </h2>
      {latestDay === undefined ? (
        <p className="m-0 text-md leading-normal text-muted">The run has not happened yet. It runs every night at 00:30 Kampala time.</p>
      ) : (
        <>
          <p className="m-0 text-sm text-muted">
            {formatDay(latestDay)}
            {late ? ", not today's. Today's run has not finished yet." : ", runs every night at 00:30 Kampala time"}
          </p>
          {latest.map((run) => (
            <dl key={run.currency} className="m-0 flex flex-col gap-2.5">
              <Row name={`Investments, ${run.currency}`} value={run.investments.toLocaleString("en-GB")} />
              <Row name="Interest to clients" value={formatExact(run.net)} />
              <Row name="Tax withheld" value={formatExact(run.tax)} />
              <Row name="Errors" value={run.behind === 0 ? "None" : `${run.behind} behind, retried tomorrow`} warn={run.behind > 0} />
            </dl>
          ))}
        </>
      )}
    </section>
  );
}

function Maturing({ investments }: { investments: readonly Investment[] }) {
  return (
    <section aria-labelledby="maturing" className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-5">
      <h2 id="maturing" className="m-0 text-[17px] font-extrabold">
        Maturing this week
      </h2>
      {investments.length === 0 ? (
        <p className="m-0 text-md text-muted">Nothing matures in the next seven days.</p>
      ) : (
        <dl className="m-0 flex flex-col gap-2.5">
          {investments.map((investment) => (
            <Row
              key={investment.id}
              name={`${formatDay(investment.maturityDate ?? "")}, ${investment.productName}`}
              value={`${formatExact(investment.amount)}, ${investment.atMaturity ? AT_MATURITY_LABELS[investment.atMaturity].toLowerCase() : "open"}`}
            />
          ))}
        </dl>
      )}
    </section>
  );
}

function RunHistory({ runs }: { runs: readonly InterestRun[] }) {
  if (runs.length === 0) return null;
  return (
    <section aria-labelledby="run-history" className="flex flex-col gap-3">
      <h2 id="run-history" className="m-0 text-[17px] font-extrabold">
        Run history
      </h2>
      <div className="overflow-x-auto rounded-xl border border-border bg-surface">
        <table className="w-full min-w-[720px] border-collapse text-md">
          <caption className="sr-only">Interest run history</caption>
          <thead>
            <tr className="text-left text-xs text-muted">
              {["Day", "Currency", "Investments", "Interest before tax", "Tax withheld", "To clients", "Errors", "Finished"].map((heading) => (
                <th key={heading} scope="col" className="px-3 py-3.5 font-bold">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={`${run.runDate}:${run.currency}`} className="border-t border-divider tabular-nums">
                <th scope="row" className="px-3 py-3 text-left font-bold whitespace-nowrap">
                  {formatDay(run.runDate)}
                </th>
                <td className="px-3 py-3">{run.currency}</td>
                <td className="px-3 py-3">{run.investments.toLocaleString("en-GB")}</td>
                <td className="px-3 py-3 whitespace-nowrap">{formatExact(run.gross)}</td>
                <td className="px-3 py-3 whitespace-nowrap">{formatExact(run.tax)}</td>
                <td className="px-3 py-3 whitespace-nowrap font-bold">{formatExact(run.net)}</td>
                <td className={cn("px-3 py-3", run.behind > 0 && "font-bold text-warning-text")}>{run.behind}</td>
                <td className="px-3 py-3 whitespace-nowrap">{run.finishedAt ? formatStamp(run.finishedAt) : "Still running"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** The client's name and account number, or their ID until the card is read or when it cannot be. */
function ClientName({ clientId, card }: { clientId: string; card: ClientCard | null | undefined }) {
  if (!card) return <span className="font-mono text-sm font-bold whitespace-nowrap">{clientId}</span>;
  return (
    <>
      <div className="font-bold whitespace-nowrap">{card.name}</div>
      <div className="text-xs text-muted">{card.accountNumber ?? "No account number yet"}</div>
    </>
  );
}

function Row({ name, value, warn = false }: { name: string; value: string; warn?: boolean }) {
  return (
    <div className="flex justify-between gap-3 border-t border-divider pt-2.5 text-md">
      <dt className="text-muted">{name}</dt>
      <dd className={cn("m-0 text-right font-bold tabular-nums", warn && "text-warning-text")}>{value}</dd>
    </div>
  );
}

export default function InvestmentsPage() {
  // Sample investments under `pnpm dev` or a preview build, otherwise the real API. `?sample=empty` shows a new system.
  const [{ api, directory }] = useState<{ api: InvestmentsApi; directory: Directory }>(() => {
    if (isStubAuth()) {
      const sample = new URLSearchParams(window.location.search).get("sample") === "empty" ? "empty" : "default";
      return { api: createStubInvestmentsApi(sample), directory: emptyDirectory };
    }
    const base = apiBaseUrl();
    if (!base) return { api: notConnectedInvestmentsApi, directory: emptyDirectory };
    const http = createHttp(base);
    return { api: createHttpInvestmentsApi(http), directory: createHttpDirectory(http) };
  });
  return (
    <>
      <InvestmentsScreen api={api} directory={directory} />
      <CorrectionsPanel />
    </>
  );
}
