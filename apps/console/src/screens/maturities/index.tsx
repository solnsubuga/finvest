import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { isStubAuth } from "@/auth/index.ts";
import { Button } from "@/components/ui/button.tsx";
import { emptyDirectory, type ClientCard, type Directory } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { cn } from "@/lib.ts";
import { PageHeader } from "@/pages/PageHeader.tsx";
import type { Investment } from "../investments/api.ts";
import { formatDay, formatExact } from "../products/format.ts";
import type { Amount, Currency } from "../products/api.ts";
import { maturitiesErrorMessage, notConnectedMaturitiesApi, type Maturities, type MaturitiesApi, type MaturityDay } from "./api.ts";
import { createHttpMaturitiesApi } from "./http.ts";
import { createStubMaturitiesApi, type StubCase } from "./stub.ts";

const SUMMARY = "Terms that end, what each one became, and anything that needs a person.";

/** Why money waits in payouts after a term ended, and what the person on the phone can do. */
const HELD_REASONS: Record<string, { label: string; action: string }> = {
  no_destination: {
    label: "Chose to be paid, but never said where",
    action: "Ask the client to choose a payout account in the app. The money goes as soon as they do.",
  },
  product_not_open: {
    label: "Chose to roll over, but the product no longer takes new money",
    action: "Ask the client to choose where to be paid in the app.",
  },
};

function heldReason(refusal: string | null) {
  return (
    HELD_REASONS[refusal ?? ""] ?? {
      label: refusal ? `Payouts refused it (${refusal})` : "Waiting for the client",
      action: "Ask the client to check their payout account in the app, then choose where to be paid.",
    }
  );
}

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; value: Maturities };

function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

/** The money that comes back at maturity: principal and interest after tax. */
function proceeds(investment: Investment): Amount {
  return { minor: investment.amount.minor + investment.interestNet.minor, currency: investment.amount.currency };
}

function totals(investments: readonly Investment[], amount: (i: Investment) => Amount): string {
  const sums = new Map<Currency, number>();
  for (const investment of investments) {
    const value = amount(investment);
    sums.set(value.currency, (sums.get(value.currency) ?? 0) + value.minor);
  }
  return [...sums].map(([currency, minor]) => formatExact({ minor, currency })).join(" and ");
}

const count = (value: number) => value.toLocaleString("en-GB");
const terms = (value: number) => `${count(value)} ${value === 1 ? "term" : "terms"}`;

interface MaturitiesScreenProps {
  api: MaturitiesApi;
  /** Names each client. Without it, a client shows as their ID. */
  directory?: Directory;
}

export function MaturitiesScreen({ api, directory = emptyDirectory }: MaturitiesScreenProps) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [cards, setCards] = useState<Record<string, ClientCard | null>>({});

  const fetchAll = useCallback(
    (): Promise<Load> =>
      api.getMaturities().then(
        (value) => ({ state: "ready", value }),
        (error: unknown) => ({ state: "failed", message: maturitiesErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchAll().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchAll]);

  const ready = load.state === "ready" ? load.value : null;
  useEffect(() => {
    if (!ready) return;
    let current = true;
    for (const clientId of new Set([...ready.notClosed, ...ready.held].map((i) => i.clientId))) {
      void directory.client(clientId).then(
        (card) => current && setCards((known) => ({ ...known, [clientId]: card })),
        () => current && setCards((known) => ({ ...known, [clientId]: null })),
      );
    }
    return () => {
      current = false;
    };
  }, [ready, directory]);

  if (load.state === "loading") {
    return (
      <>
        <PageHeader title="Maturities" summary={SUMMARY} />
        <p role="status" className="m-0 text-md text-muted">
          Loading maturities
        </p>
      </>
    );
  }
  if (load.state === "failed") {
    return (
      <>
        <PageHeader title="Maturities" summary={SUMMARY} />
        <section role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">Maturities could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <Button variant="secondary" onClick={() => void fetchAll().then(setLoad)}>
            Try again
          </Button>
        </section>
      </>
    );
  }

  const { today, days, notClosed, held } = load.value;
  const dueToday = days.filter((d) => d.day === today);
  return (
    <>
      <PageHeader title="Maturities" summary={SUMMARY} />
      <div className="grid grid-cols-[repeat(auto-fit,minmax(280px,1fr))] items-start gap-4">
        <RunCard today={today} dueToday={dueToday} notClosed={notClosed} />
        <HeldCard held={held} />
      </div>
      {notClosed.length > 0 ? <NotClosed investments={notClosed} cards={cards} today={today} /> : null}
      {held.length > 0 ? <Held investments={held} cards={cards} /> : null}
      <DayByDay days={days} today={today} />
    </>
  );
}

function Card({ title, warn, children }: { title: string; warn: boolean; children: React.ReactNode }) {
  const id = `card-${title.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  return (
    <section
      aria-labelledby={id}
      className={cn("flex flex-col gap-3 rounded-xl bg-surface p-5", warn ? "border-2 border-warning p-[19px]" : "border border-border")}
    >
      <h2 id={id} className="m-0 text-[17px] font-extrabold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function RunCard({ today, dueToday, notClosed }: { today: string; dueToday: readonly MaturityDay[]; notClosed: readonly Investment[] }) {
  const closedToday = dueToday.reduce((sum, d) => sum + d.rolledOver + d.paidOut + d.held, 0);
  const openToday = dueToday.reduce((sum, d) => sum + d.openRollOver + d.openPayOut, 0);
  return (
    <Card title="Maturity run" warn={notClosed.length > 0}>
      <p className="m-0 text-sm text-muted">{formatDay(today)}. The run closes each term the morning after its last day of interest.</p>
      {notClosed.length > 0 ? (
        <p className="m-0 text-md leading-normal font-bold text-warning-text">
          {terms(notClosed.length)} past {notClosed.length === 1 ? "its" : "their"} maturity date {notClosed.length === 1 ? "is" : "are"} still running.
          Tell an engineer: the run tries again every hour, but these need a look.
        </p>
      ) : (
        <p className="m-0 text-md leading-normal">Every term that ended before today has been closed.</p>
      )}
      <dl className="m-0 flex flex-col gap-2.5">
        <Row name="Ending today" value={dueToday.length === 0 ? "None" : terms(closedToday + openToday)} />
        {dueToday.map((d) => (
          <Row key={d.currency} name={`Principal, ${d.currency}`} value={formatExact(d.principal)} />
        ))}
        {closedToday > 0 ? <Row name="Already closed" value={terms(closedToday)} /> : null}
      </dl>
    </Card>
  );
}

function HeldCard({ held }: { held: readonly Investment[] }) {
  return (
    <Card title="Held for the client" warn={held.length > 0}>
      {held.length === 0 ? (
        <p className="m-0 text-md leading-normal text-muted">No money is waiting. Every matured term was rolled over or paid out.</p>
      ) : (
        <>
          <p className="m-0 text-md leading-normal">
            {terms(held.length)} ended and the money waits in payouts until the client says where to send it. It is safe, and earns nothing while it waits.
          </p>
          <dl className="m-0 flex flex-col gap-2.5">
            <Row name="Waiting" value={totals(held, proceeds)} warn />
          </dl>
        </>
      )}
    </Card>
  );
}

const TABLE = "w-full min-w-[720px] border-collapse text-md";
const HEAD = "px-3 py-3.5 font-bold";
const CELL = "px-3 py-3";

function Table({ id, title, caption, headings, children }: { id: string; title: string; caption: string; headings: readonly string[]; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="m-0 text-[17px] font-extrabold">
        {title}
      </h2>
      <div className="overflow-x-auto rounded-xl border border-border bg-surface">
        <table className={TABLE}>
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr className="text-left text-xs text-muted">
              {headings.map((heading) => (
                <th key={heading} scope="col" className={HEAD}>
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>{children}</tbody>
        </table>
      </div>
    </section>
  );
}

function NotClosed({ investments, cards, today }: { investments: readonly Investment[]; cards: Readonly<Record<string, ClientCard | null>>; today: string }) {
  return (
    <Table
      id="not-closed"
      title="Not closed by the run"
      caption="Terms past their maturity date that are still running"
      headings={["Client", "Product", "Invested", "Ended", "Client chose"]}
    >
      {investments.map((investment) => {
        const late = investment.maturityDate ? daysBetween(investment.maturityDate, today) : 0;
        return (
          <tr key={investment.id} className="border-t border-divider align-top tabular-nums">
            <th scope="row" className={cn(CELL, "text-left font-normal")}>
              <ClientName clientId={investment.clientId} card={cards[investment.clientId]} />
            </th>
            <td className={cn(CELL, "font-bold whitespace-nowrap")}>{investment.productName}</td>
            <td className={cn(CELL, "whitespace-nowrap")}>{formatExact(investment.amount)}</td>
            <td className={cn(CELL, "whitespace-nowrap")}>
              <div>{investment.maturityDate ? formatDay(investment.maturityDate) : ""}</div>
              <div className="text-xs font-bold text-warning-text">
                {late} {late === 1 ? "day" : "days"} ago
              </div>
            </td>
            <td className={CELL}>{investment.atMaturity === "pay_out" ? "Pay out" : "Roll over"}</td>
          </tr>
        );
      })}
    </Table>
  );
}

function Held({ investments, cards }: { investments: readonly Investment[]; cards: Readonly<Record<string, ClientCard | null>> }) {
  return (
    <Table id="held" title="Money held for the client" caption="Matured terms whose money waits for the client" headings={["Client", "Product", "Waiting", "Ended", "Why it waits"]}>
      {investments.map((investment) => {
        const reason = heldReason(investment.payoutRefusal);
        return (
          <tr key={investment.id} className="border-t border-divider align-top tabular-nums">
            <th scope="row" className={cn(CELL, "text-left font-normal")}>
              <ClientName clientId={investment.clientId} card={cards[investment.clientId]} />
            </th>
            <td className={cn(CELL, "font-bold whitespace-nowrap")}>{investment.productName}</td>
            <td className={cn(CELL, "whitespace-nowrap")}>
              <div className="font-bold">{formatExact(proceeds(investment))}</div>
              <div className="text-xs text-muted">
                {formatExact(investment.amount)} and {formatExact(investment.interestNet)} interest
              </div>
            </td>
            <td className={cn(CELL, "whitespace-nowrap")}>{investment.maturityDate ? formatDay(investment.maturityDate) : ""}</td>
            <td className={cn(CELL, "min-w-[260px]")}>
              <div className="font-bold">{reason.label}</div>
              <div className="text-sm leading-normal text-muted">{reason.action}</div>
            </td>
          </tr>
        );
      })}
    </Table>
  );
}

function DayByDay({ days, today }: { days: readonly MaturityDay[]; today: string }) {
  if (days.length === 0) {
    return (
      <section aria-labelledby="day-by-day" className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-5">
        <h2 id="day-by-day" className="m-0 text-[17px] font-extrabold">
          No term ends this week or the next two
        </h2>
        <p className="m-0 text-md leading-normal text-muted">Terms appear here a week before and after the day they end.</p>
      </section>
    );
  }
  return (
    <Table
      id="day-by-day"
      title="Day by day"
      caption="Terms ending each day, from a week ago to two weeks ahead"
      headings={["Day", "Currency", "Terms", "Principal", "What happened", "Still to close"]}
    >
      {days.map((d) => {
        const closed = d.rolledOver + d.paidOut + d.held;
        const open = d.openRollOver + d.openPayOut;
        const isToday = d.day === today;
        const late = d.day < today && open > 0;
        return (
          <tr key={`${d.day}:${d.currency}`} className={cn("border-t border-divider align-top tabular-nums", isToday && "bg-surface-sunken")}>
            <th scope="row" className={cn(CELL, "text-left font-bold whitespace-nowrap")}>
              {formatDay(d.day)}
              {isToday ? <span className="ml-2 text-xs font-bold text-muted">Today</span> : null}
            </th>
            <td className={CELL}>{d.currency}</td>
            <td className={CELL}>{count(d.investments)}</td>
            <td className={cn(CELL, "whitespace-nowrap")}>{formatExact(d.principal)}</td>
            <td className={CELL}>
              {closed === 0 ? (
                <span className="text-muted">{d.day > today ? "Not due yet" : "Nothing closed yet"}</span>
              ) : (
                <Outcomes parts={[[d.rolledOver, "rolled over"], [d.paidOut, "paid out"], [d.held, "held"]]} warnLast={d.held > 0} />
              )}
            </td>
            <td className={cn(CELL, late && "font-bold text-warning-text")}>
              {open === 0 ? <span className="text-muted">None</span> : <Outcomes parts={[[d.openRollOver, "to roll over"], [d.openPayOut, "to pay out"]]} />}
            </td>
          </tr>
        );
      })}
    </Table>
  );
}

function Outcomes({ parts, warnLast = false }: { parts: readonly (readonly [number, string])[]; warnLast?: boolean }) {
  const shown = parts.filter(([value]) => value > 0);
  return (
    <span>
      {shown.map(([value, label], index) => (
        <span key={label} className={cn(warnLast && label === "held" && "font-bold text-warning-text")}>
          {index > 0 ? ", " : ""}
          {count(value)} {label}
        </span>
      ))}
    </span>
  );
}

/** The client's name and account number, linked to their page, or their ID until the card is read. */
function ClientName({ clientId, card }: { clientId: string; card: ClientCard | null | undefined }) {
  const to = `/clients?client=${encodeURIComponent(clientId)}`;
  if (!card) {
    return (
      <Link to={to} className="font-mono text-sm font-bold whitespace-nowrap">
        {clientId}
      </Link>
    );
  }
  return (
    <>
      <Link to={to} className="font-bold whitespace-nowrap">
        {card.name}
      </Link>
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

function stubCase(): StubCase {
  const sample = new URLSearchParams(window.location.search).get("sample");
  return sample === "empty" || sample === "error" ? sample : "sample";
}

export default function MaturitiesPage() {
  // Sample maturities under `pnpm dev` or a preview build, otherwise the real API.
  // `?sample=empty` shows a new system and `?sample=error` a failed read.
  const [{ api, directory }] = useState<{ api: MaturitiesApi; directory: Directory }>(() => {
    if (isStubAuth()) return { api: createStubMaturitiesApi(stubCase()), directory: createStubDirectory() };
    const base = apiBaseUrl();
    if (!base) return { api: notConnectedMaturitiesApi, directory: emptyDirectory };
    const http = createHttp(base);
    return { api: createHttpMaturitiesApi(http), directory: createHttpDirectory(http) };
  });
  return <MaturitiesScreen api={api} directory={directory} />;
}
