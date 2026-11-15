import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";
import { cn } from "@/lib.ts";
import type { Amount } from "../api.ts";
import { potTitle } from "../dayclose/DayClose.tsx";
import { formatDay, formatExact } from "../format.ts";
import { clientMoneyErrorMessage, type CheckLine, type ClientMoneyApi, type ClientMoneyCheck } from "./api.ts";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; check: ClientMoneyCheck | null };

/**
 * Staff wording for the ledger's codes. The ledger's own account name is shown for any other code.
 * The carried-over account's ledger name refers to the old platform, which staff copy never names.
 */
const LINE_NAMES: Record<string, string> = {
  "migration.opening_balances": "Balances carried over from the paper register, not yet in a pot",
  "receivables.deposit_correction": "Owed by clients for payments credited by mistake",
  "dealing.buffer_capital": "Finvest's own money in the buffer",
  "tax.withholding_payable": "Tax withheld from interest, for URA",
  earnings: "Finvest's earnings so far, income less costs",
};

/** Carried-over accounts are named by the old platform in the ledger, so any such code gets a neutral name. */
const lineName = (line: CheckLine) =>
  LINE_NAMES[line.code] ?? (line.code.startsWith("migration.") ? "Balances carried over from the paper register" : line.name);

/** A row under held: what is taken off, or, for earnings below zero, the costs above income that are added back. */
function heldLabel(line: CheckLine): string {
  if (line.amount.minor >= 0) return `Less: ${lineName(line)}`;
  return line.code === "earnings" ? "Plus: Finvest's costs so far above its income" : `Plus: ${lineName(line)}, below zero`;
}
const negate = (amount: Amount): Amount => ({ ...amount, minor: -amount.minor });
const absolute = (amount: Amount): Amount => ({ ...amount, minor: Math.abs(amount.minor) });

/** What Finvest owes its clients against what it holds for them, for the last closed day. Read only. */
export function ClientMoney({ api }: { api: ClientMoneyApi }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const fetchCheck = useCallback(
    (): Promise<Load> =>
      api.get().then(
        (check): Load => ({ state: "ready", check }),
        (error: unknown): Load => ({ state: "failed", message: clientMoneyErrorMessage(error) }),
      ),
    [api],
  );

  useEffect(() => {
    let current = true;
    void fetchCheck().then((found) => current && setLoad(found));
    return () => {
      current = false;
    };
  }, [fetchCheck]);

  return (
    <section aria-labelledby="client-money-title" className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-5">
      <div className="flex flex-col gap-1">
        <h2 id="client-money-title" className="m-0 text-xl font-extrabold">
          Client money check
        </h2>
        <p className="m-0 text-md text-muted">
          What we owe clients, from the ledger, against what we hold for them, from the statements of the last day two people closed and the
          custodian.
        </p>
      </div>
      {load.state === "loading" ? (
        <p role="status" className="m-0 text-md text-muted">
          Loading the client money check
        </p>
      ) : load.state === "failed" ? (
        <div role="alert" className="flex flex-col items-start gap-2">
          <p className="m-0 text-md font-bold">The client money check could not be loaded</p>
          <p className="m-0 text-md text-muted">{load.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              setLoad({ state: "loading" });
              void fetchCheck().then(setLoad);
            }}
          >
            Try again
          </Button>
        </div>
      ) : load.check === null ? (
        <p role="status" className="m-0 text-md text-muted">
          No day has been closed yet. The check reads the statements of the last day two people closed, so it appears once the first day is closed
          below.
        </p>
      ) : (
        <Figures check={load.check} />
      )}
    </section>
  );
}

function Figures({ check }: { check: ClientMoneyCheck }) {
  const day = formatDay(check.businessDate);
  const custodian = check.custodian;
  const custodianNote =
    custodian === null
      ? { text: "No custodian statement check has been signed off yet, so the bills are as the dealing desk's books have them.", warn: true }
      : {
          text: `${custodian.statementDate === check.businessDate ? "Custodian statement checked for this day" : `Custodian statement last checked for ${formatDay(custodian.statementDate)}`}${
            custodian.differences === 0
              ? ", every maturity matching."
              : `, with ${custodian.differences === 1 ? "1 difference" : `${custodian.differences} differences`} explained before sign-off.`
          }`,
          warn: custodian.statementDate !== check.businessDate || custodian.differences > 0,
        };
  return (
    <>
      <Standing check={check} day={day} />
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,380px),1fr))] items-start gap-4">
        <Ledger title="Owed to clients" source="From the ledger, every client's accounts added up">
          <Row label="Invested, paid in, and payouts on their way" amount={{ ...check.owed, minor: check.owed.minor - check.interestOwed.minor }} />
          <Row
            label="Interest earned, not yet paid"
            amount={check.interestOwed}
            note={`Paid for by the discount on the bills when they mature: ${formatExact(check.discountToCome)} still to come on the bills held.`}
          />
          <Row label="Owed to clients" amount={check.owed} total />
        </Ledger>
        <Ledger title="Held for clients" source={`From the statements for ${day} and the custodian`}>
          {check.pots.map((pot) => (
            <Row
              key={`${pot.source}:${pot.accountReference}`}
              label={`${potTitle(pot)}, statement`}
              amount={pot.statementClosing}
              note={
                pot.openDifferences.minor === 0
                  ? undefined
                  : `The statement stands ${formatExact(absolute(pot.openDifferences))} ${pot.openDifferences.minor > 0 ? "above" : "below"} the books, still open in the day close.`
              }
            />
          ))}
          <Row label="Bills and bonds at the custodian, at cost" amount={check.bills} note={custodianNote.text} warn={custodianNote.warn} />
          {check.notClientMoney.map((line) => (
            <Row
              key={line.code}
              label={heldLabel(line)}
              amount={negate(line.amount)}
              note={
                line.amount.minor < 0 && line.code === "earnings"
                  ? "Mostly interest clients have earned, which the bills pay for when they mature."
                  : undefined
              }
            />
          ))}
          <Row label="Held for clients" amount={check.held} total />
        </Ledger>
      </div>
      {check.difference.minor === 0 ? null : <Accounted check={check} />}
    </>
  );
}

function Standing({ check, day }: { check: ClientMoneyCheck; day: string }) {
  const words =
    check.standing === "ties"
      ? { pill: "Ties", line: `At the end of ${day}, what we hold for clients equals what we owe them, to the shilling.` }
      : check.standing === "short"
        ? { pill: `Short by ${formatExact(absolute(check.difference))}`, line: `At the end of ${day}, we hold less for clients than we owe them.` }
        : { pill: `Over by ${formatExact(check.difference)}`, line: `At the end of ${day}, we hold more for clients than we owe them.` };
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span
        className={cn(
          "rounded-pill px-3 py-1 text-sm font-bold whitespace-nowrap",
          check.standing === "ties" ? "bg-success-surface text-success" : check.standing === "short" ? "bg-danger-surface text-danger" : "bg-warning-surface text-warning-text",
        )}
      >
        {words.pill}
      </span>
      <p className="m-0 text-md">{words.line}</p>
    </div>
  );
}

function Accounted({ check }: { check: ClientMoneyCheck }) {
  const unexplained = check.unexplained.minor !== 0;
  return (
    <section aria-labelledby="client-money-why" className="flex flex-col gap-2 rounded-lg border border-border-strong p-4">
      <h3 id="client-money-why" className="m-0 text-lg font-extrabold">
        Why it does not tie
      </h3>
      <dl className="m-0 flex flex-col gap-1.5">
        {check.openDifferences.minor === 0 ? null : (
          <Row
            label="Differences still open on the statements"
            amount={check.openDifferences}
            note="The statements stand this far from the books. Each one is listed under Close the day, and is put right by an entry that is then matched."
          />
        )}
        {check.notOnStatement.map((line) => (
          <Row
            key={line.code}
            label={`On the books, on no statement: ${lineName(line)}`}
            amount={negate(line.amount)}
            note="Counted as owed but not as held, until the money is in a pot."
          />
        ))}
        {unexplained ? (
          <Row
            label="Not accounted for"
            amount={check.unexplained}
            warn
            note="Neither the open differences nor the books account for this. Tell the finance lead: a figure may have been read wrongly."
          />
        ) : null}
      </dl>
    </section>
  );
}

function Ledger({ title, source, children }: { title: string; source: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col gap-2 rounded-lg border border-border p-4">
      <div className="flex flex-col gap-0.5">
        <h3 className="m-0 text-lg font-extrabold">{title}</h3>
        <p className="m-0 text-sm text-muted">{source}</p>
      </div>
      <dl className="m-0 flex flex-col gap-1.5">{children}</dl>
    </section>
  );
}

function Row({ label, amount, note, total = false, warn = false }: { label: string; amount: Amount; note?: string | undefined; total?: boolean; warn?: boolean }) {
  return (
    <div className={cn("flex flex-col gap-0.5", total && "border-t border-divider pt-2")}>
      <div className="flex items-baseline justify-between gap-3">
        <dt className={cn("text-md", total && "font-extrabold")}>{label}</dt>
        <dd className={cn("m-0 text-right whitespace-nowrap tabular-nums", total ? "font-extrabold" : "font-semibold")}>{formatExact(amount)}</dd>
      </div>
      {note ? <dd className={cn("m-0 text-sm", warn ? "font-bold text-warning-text" : "text-muted")}>{note}</dd> : null}
    </div>
  );
}
