import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import type { Currency } from "./api.ts";
import {
  booksErrorMessage,
  type Books,
  type BooksApi,
  type Days,
  type FormatOption,
  type IncomeLine,
  type JournalFile,
  type JournalFormat,
} from "./books.ts";
import { Figure } from "./Figure.tsx";
import { formatAmount, formatDay, formatMonth, formatNumber } from "./format.ts";

const MONTHS_OFFERED = 12;

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; books: Books };

function monthsBack(today: string, count: number): string[] {
  const [year, month] = today.split("-").map(Number) as [number, number];
  return Array.from({ length: count }, (_, back) => {
    const index = year * 12 + (month - 1) - back;
    return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
  });
}

const FILE_DESCRIPTIONS: Record<JournalFormat, string> = {
  csv: "Daily journal by Finvest account, for any system",
  quickbooks: "Journal entry import, one entry per day",
  xero: "Manual journal import, one journal per day",
  sage: "Journal import, one journal per day",
  zoho: "Manual journal import, one draft journal per day",
};

function formatName(formats: readonly FormatOption[], format: JournalFormat): string {
  return formats.find((option) => option.format === format)?.name ?? "Finvest CSV";
}

/** Why a format cannot be downloaded now, or null when it can. */
function blocked(option: FormatOption | undefined, currency: Currency): string | null {
  if (!option) return null;
  if (!option.currencies.includes(currency)) return `${option.name} takes the journal in shillings only. Download dollar journals as the Finvest CSV.`;
  if (!option.ready) return `No ${option.name} account codes are approved yet. An admin sets them under Settings, Accounting system codes, and a second admin approves them.`;
  return null;
}

function daysLabel(dateFrom: string, dateTo: string): string {
  return dateFrom === dateTo ? formatDay(dateFrom) : `${formatDay(dateFrom)} to ${formatDay(dateTo)}`;
}

interface BooksSectionProps {
  api: BooksApi;
  currency: Currency;
  today: string;
  save: (file: JournalFile) => void;
}

/**
 * The books by ledger code for a month, Finvest's own income and costs, and the
 * daily journal for the accounting system. Reads only: nothing here moves money,
 * so one person downloads the journal. The file is then imported by hand.
 */
export function BooksSection({ api, currency, today, save }: BooksSectionProps) {
  const thisMonth = today.slice(0, 7);
  const [month, setMonth] = useState(thisMonth);
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  // Null until chosen: then the format of the last download, or the Finvest CSV.
  const [chosen, setChosen] = useState<JournalFormat | null>(null);

  const fetchBooks = useCallback(
    (): Promise<Load> =>
      api.getBooks(currency, month).then(
        (books) => ({ state: "ready", books }),
        (error: unknown) => ({ state: "failed", message: booksErrorMessage(error) }),
      ),
    [api, currency, month],
  );

  useEffect(() => {
    let current = true;
    void fetchBooks().then((next) => current && setLoad(next));
    return () => {
      current = false;
    };
  }, [fetchBooks]);

  // The server records each download as it answers, so a file lost on the way can be
  // downloaded again from its row. Saving is a separate step that can fail on its own.
  async function download(days: Days, id: string, format: JournalFormat) {
    setDownloading(id);
    setDownloadError(null);
    let file: JournalFile;
    try {
      file = await api.exportJournal(currency, days, format);
    } catch (error) {
      setDownloadError(booksErrorMessage(error));
      setDownloading(null);
      return;
    }
    try {
      save(file);
    } catch {
      setDownloadError(`The file for ${daysLabel(days.dateFrom, days.dateTo)} could not be saved. Download it again from the list below.`);
    }
    const fresh = await fetchBooks();
    if (fresh.state === "ready") setLoad(fresh);
    setDownloading(null);
  }

  const monthPicker = (
    <label className="flex items-center gap-2 text-sm font-bold">
      Month
      <select
        className="rounded-lg border border-border-strong bg-surface px-3 py-2 text-md font-medium text-ink"
        value={month}
        onChange={(event) => {
          setLoad({ state: "loading" });
          setDownloadError(null);
          setMonth(event.target.value);
        }}
      >
        {monthsBack(today, MONTHS_OFFERED).map((option) => (
          <option key={option} value={option}>
            {formatMonth(option)}
          </option>
        ))}
      </select>
    </label>
  );

  if (load.state === "loading") {
    return (
      <section aria-label="Books" className="flex flex-col gap-4">
        <p role="status" className="m-0 text-md text-muted">
          Loading the books
        </p>
      </section>
    );
  }
  if (load.state === "failed") {
    return (
      <section aria-label="Books" className="flex flex-col gap-4">
        <div role="alert" className="flex max-w-[640px] flex-col items-start gap-3 rounded-xl border border-border bg-surface p-5">
          <h2 className="m-0 text-xl font-extrabold">The books could not be loaded</h2>
          <p className="m-0 text-md leading-normal text-muted">{load.message}</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => void fetchBooks().then(setLoad)}>
              Try again
            </Button>
            {month !== thisMonth ? (
              <Button
                variant="secondary"
                onClick={() => {
                  setLoad({ state: "loading" });
                  setMonth(thisMonth);
                }}
              >
                Back to this month
              </Button>
            ) : null}
          </div>
        </div>
      </section>
    );
  }

  const { books } = load;
  const period = month === thisMonth ? "this month" : `in ${formatMonth(month)}`;
  const latest = books.exports[0];
  const format: JournalFormat = chosen ?? (latest && !blocked(books.formats.find((f) => f.format === latest.format), currency) ? latest.format : "csv");
  const option = books.formats.find((f) => f.format === format);
  const cannot = blocked(option, currency);

  return (
    <section aria-label="Books" className="flex flex-col gap-5">
      <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4">
        <Figure name={`Income ${period}`} value={formatAmount(books.income.incomeTotal)} note="Earned on bills held and sold" />
        <Figure name={`Costs ${period}`} value={formatAmount(books.income.costTotal)} note="Interest to clients, charges and losses" />
        <Figure
          name={`Net ${period}`}
          value={formatAmount(books.income.net)}
          note={books.income.net.minor < 0 ? "Costs are above income" : "Income less costs"}
          warn={books.income.net.minor < 0}
        />
        <Figure
          name="Journal downloaded through"
          value={books.sentThrough ? formatDay(books.sentThrough) : "Nothing downloaded yet"}
          note={books.closedThrough ? `Books closed through ${formatDay(books.closedThrough)}` : "No day closed yet"}
          warn={books.nextExport !== null}
        />
      </dl>

      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[3_1_480px] flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="m-0 text-[17px] font-extrabold">Books for {formatMonth(month)}</h2>
            {monthPicker}
          </div>
          {books.accounts.length === 0 ? (
            <p className="m-0 rounded-xl border border-border bg-surface p-5 text-md leading-normal text-muted">
              Nothing is in the {currency === "UGX" ? "shilling" : "dollar"} books for {formatMonth(month)} yet. Each account appears here once money first moves
              through it.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-border bg-surface" tabIndex={0} role="region" aria-label={`Books for ${formatMonth(month)}`}>
              <table aria-label={`Books for ${formatMonth(month)}`} className="w-full min-w-[640px] border-collapse text-md">
                <caption className="sr-only">
                  Each ledger account, all clients together, from {formatDay(books.dateFrom)} to {formatDay(books.dateTo)}, in {currency}
                </caption>
                <thead>
                  <tr className="text-left text-xs text-muted">
                    <th scope="col" className="px-3 py-3.5 font-bold">
                      Account
                    </th>
                    {["Opening", "Debits", "Credits", "Closing"].map((heading) => (
                      <th key={heading} scope="col" className="px-3 py-3.5 text-right font-bold">
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {books.accounts.map((line) => (
                    <tr key={line.code} className="border-t border-border-subtle">
                      <th scope="row" className="px-3 py-3 text-left font-bold">
                        {line.name}
                        <span className="block text-xs font-medium text-muted">{line.code}</span>
                      </th>
                      {[line.opening, line.debits, line.credits, line.closing].map((amount, index) => (
                        <td key={index} className="px-3 py-3 text-right tabular-nums">
                          {formatNumber(amount)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="m-0 text-sm leading-normal text-muted">
            {currency} from {formatDay(books.dateFrom)} to {formatDay(books.dateTo)}. Opening and closing are each account&rsquo;s balance the usual way round: what
            Finvest holds, owes or has earned. A client&rsquo;s accounts are added into their line, so no client is named.
          </p>
        </div>

        <aside aria-label="Income and the accounting export" className="flex min-w-0 flex-[2_1_320px] flex-col gap-4">
          <div className="flex flex-col gap-2.5 rounded-[20px] border border-border bg-surface p-5">
            <h2 className="m-0 text-[17px] font-extrabold">Income and costs</h2>
            <Lines heading="Income" lines={books.income.income} empty="No income yet" />
            <Lines heading="Costs" lines={books.income.costs} empty="No costs yet" />
            <Row name="Net" value={formatAmount(books.income.net)} strong />
            <p className="m-0 text-sm leading-normal text-muted">
              Finvest charges clients no fees. ioTec&rsquo;s charge on each mobile money payment in is not booked yet, so it is missing from the costs until the
              accountant agrees how to book it.
            </p>
          </div>

          <div className="flex flex-col gap-2.5 rounded-[20px] border border-border bg-surface p-5">
            <h2 className="m-0 text-[17px] font-extrabold">Accounting export</h2>
            <label className="flex flex-col gap-1.5 border-t border-border-subtle pt-2.5 text-sm font-bold">
              File for
              <select
                className="rounded-lg border border-border-strong bg-surface px-3 py-2 text-md font-medium text-ink"
                value={format}
                onChange={(event) => {
                  setChosen(event.target.value as JournalFormat);
                  setDownloadError(null);
                }}
              >
                {books.formats.map((f) => (
                  <option key={f.format} value={f.format}>
                    {f.name}
                    {f.format !== "csv" && !f.ready ? " (not set up)" : ""}
                  </option>
                ))}
              </select>
              <span className="font-medium text-muted">{FILE_DESCRIPTIONS[format]}, after the day is closed</span>
            </label>
            <Row name="Closed through" value={books.closedThrough ? formatDay(books.closedThrough) : "No day closed yet"} />
            <Row
              name="Last downloaded"
              value={latest ? `${daysLabel(latest.dateFrom, latest.dateTo)}, ${latest.lines} line${latest.lines === 1 ? "" : "s"}` : "Nothing downloaded yet"}
            />
            <p className="m-0 text-sm leading-normal text-muted">
              Nothing is sent to the accounting system from here. Download the file and import it there yourself.
            </p>
            {cannot ? (
              <p className="m-0 rounded-lg bg-surface-sunken p-3 text-sm leading-normal text-muted">{cannot}</p>
            ) : option && option.unmapped.length > 0 ? (
              <p className="m-0 rounded-lg bg-warning-surface p-3 text-sm leading-normal text-warning-text">
                {option.unmapped.length === 1 ? "1 Finvest account has" : `${option.unmapped.length} Finvest accounts have`} no {option.name} code yet:{" "}
                {option.unmapped.join(", ")}. A day that moved {option.unmapped.length === 1 ? "it" : "one of them"} cannot be downloaded for {option.name} until
                an admin adds {option.unmapped.length === 1 ? "its code" : "their codes"} in Settings.
              </p>
            ) : null}
            {books.nextExport ? (
              <Button
                className="mt-1 h-12 rounded-[24px]"
                disabled={downloading !== null || cannot !== null}
                onClick={() => books.nextExport && void download(books.nextExport, "next", format)}
              >
                {downloading === "next"
                  ? "Preparing the file"
                  : `Download ${daysLabel(books.nextExport.dateFrom, books.nextExport.dateTo)}${format === "csv" ? "" : ` for ${option?.name ?? format}`}`}
              </Button>
            ) : (
              <p className="m-0 rounded-lg bg-surface-sunken p-3 text-sm leading-normal text-muted">
                {books.closedThrough
                  ? "Every closed day has been downloaded. The next one can be once it is closed on the Reconciliation screen."
                  : "Close a day on the Reconciliation screen first. Only closed days can be downloaded, so a journal never changes after it is imported."}
              </p>
            )}
            {downloadError ? (
              <p role="alert" className="m-0 text-sm font-bold text-danger">
                {downloadError}
              </p>
            ) : null}
            {books.exports.length > 0 ? (
              <>
                <h3 className="m-0 mt-2 text-sm font-extrabold">Downloaded before</h3>
                <ul className="m-0 flex list-none flex-col gap-2 p-0">
                  {books.exports.map((past) => (
                    <li key={past.id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-border-subtle pt-2 text-sm">
                      <span className="flex flex-col">
                        <span className="font-bold">{daysLabel(past.dateFrom, past.dateTo)}</span>
                        <span className="text-muted">
                          {formatName(books.formats, past.format)}, {past.lines} line{past.lines === 1 ? "" : "s"}, by{" "}
                          {past.exportedByName ?? "a former member of staff"}
                        </span>
                      </span>
                      <Button
                        variant="secondary"
                        className="h-9"
                        disabled={downloading !== null}
                        onClick={() => void download({ dateFrom: past.dateFrom, dateTo: past.dateTo }, past.id, past.format)}
                      >
                        {downloading === past.id ? "Preparing" : "Download again"}
                        <span className="sr-only">
                          , {daysLabel(past.dateFrom, past.dateTo)}, {formatName(books.formats, past.format)}
                        </span>
                      </Button>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        </aside>
      </div>
    </section>
  );
}

function Row({ name, value, strong = false }: { name: string; value: string; strong?: boolean }) {
  return (
    <div className="flex justify-between gap-3 border-t border-border-subtle pt-2.5 text-md">
      <span className="text-muted">{name}</span>
      <span className={strong ? "text-right font-extrabold tabular-nums" : "text-right font-bold tabular-nums"}>{value}</span>
    </div>
  );
}

function Lines({ heading, lines, empty }: { heading: string; lines: readonly IncomeLine[]; empty: string }) {
  return (
    <div className="flex flex-col gap-1.5 border-t border-border-subtle pt-2.5">
      <h3 className="m-0 text-sm font-extrabold">{heading}</h3>
      {lines.length === 0 ? <p className="m-0 text-sm text-muted">{empty}</p> : null}
      {lines.map((line) => (
        <div key={line.code} className="flex justify-between gap-3 text-sm">
          <span className="text-muted">{line.name}</span>
          <span className="text-right font-bold tabular-nums">{formatAmount(line.amount)}</span>
        </div>
      ))}
    </div>
  );
}
