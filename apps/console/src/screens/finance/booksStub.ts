/**
 * Sample books for `pnpm dev` and preview builds. Not Finvest's books. The
 * figures add up as the ledger's would: each code's closing is its opening
 * plus the month's movement, and only closed days can be sent.
 */

import type { Amount, Currency } from "./api.ts";
import { BooksError, type BookLine, type Books, type BooksApi, type Days, type JournalExport } from "./books.ts";
import { journalFilename } from "./booksHttp.ts";

interface Reader {
  id: string;
  name: string;
}

export interface BooksStubOptions {
  /** Nothing in the books yet: a new Finvest. */
  empty?: boolean;
  today?: string;
}

const DEBIT_NORMAL = new Set(["asset", "expense"]);

// code, name, kind, opening, debits, credits, in shillings.
const UGX_BOOKS: readonly [string, string, string, number, number, number][] = [
  ["bank.trust", "Trust bank account", "asset", 18_146_250_000, 412_000_000, 251_300_000],
  ["dealing.bill_income", "Income on bills held to maturity", "income", 61_400_000, 0, 9_850_000],
  ["dealing.bills", "Bills and bonds held, at cost", "asset", 17_620_000_000, 248_400_000, 386_000_000],
  ["dealing.sale_gains", "Gains on bills sold", "income", 1_250_000, 0, 420_000],
  ["fees.provider.iotec", "ioTec fees", "expense", 182_000, 24_500, 0],
  ["investments.holdings", "Invested", "liability", 18_102_400_000, 248_400_000, 386_000_000],
  ["investments.interest_accrued", "Interest earned, not yet paid", "liability", 402_700_000, 21_900_000, 39_300_000],
  ["investments.interest_expense", "Interest paid to clients", "expense", 48_100_000, 6_370_000, 0],
  ["iotec.wallet", "ioTec wallet", "asset", 26_100_000, 412_000_000, 406_600_000],
  ["tax.withholding_payable", "Tax withheld, to pay URA", "liability", 31_700_000, 0, 6_900_000],
];

function shift(day: string, days: number): string {
  const moment = new Date(`${day}T00:00:00Z`);
  moment.setUTCDate(moment.getUTCDate() + days);
  return moment.toISOString().slice(0, 10);
}

function monthEnd(month: string): string {
  const [year, number] = month.split("-").map(Number) as [number, number];
  return new Date(Date.UTC(year, number, 0)).toISOString().slice(0, 10);
}

export function createStubBooksApi(reader: Reader, options: BooksStubOptions = {}): BooksApi {
  const today = options.today ?? new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());
  const closedThrough = options.empty ? null : shift(today, -2);
  const exports: JournalExport[] = options.empty
    ? []
    : [
        {
          id: "stub-export-2",
          dateFrom: shift(today, -4),
          dateTo: shift(today, -4),
          lines: 14,
          debitTotal: { minor: 221_640_000, currency: "UGX" },
          exportedBy: "stub-grace",
          exportedByName: "Grace Atim",
          exportedAt: `${shift(today, -3)}T06:12:00Z`,
        },
        {
          id: "stub-export-1",
          dateFrom: shift(today, -7),
          dateTo: shift(today, -5),
          lines: 38,
          debitTotal: { minor: 604_118_500, currency: "UGX" },
          exportedBy: "stub-grace",
          exportedByName: "Grace Atim",
          exportedAt: `${shift(today, -4)}T06:05:00Z`,
        },
      ];

  function books(currency: Currency, month: string): Books {
    const amount = (minor: number): Amount => ({ minor, currency });
    const scale = currency === "UGX" ? 1 : 0; // The sample has no dollar business yet.
    const rows = options.empty || !scale ? [] : UGX_BOOKS;
    const accounts: BookLine[] = rows.map(([code, name, kind, opening, debits, credits]) => ({
      code,
      name,
      kind,
      opening: amount(opening),
      debits: amount(debits),
      credits: amount(credits),
      closing: amount(opening + (DEBIT_NORMAL.has(kind) ? debits - credits : credits - debits)),
    }));
    const moved = (kind: string) =>
      accounts
        .filter((a) => a.kind === kind && a.closing.minor !== a.opening.minor)
        .map((a) => ({ code: a.code, name: a.name, amount: amount(a.closing.minor - a.opening.minor) }));
    const income = moved("income");
    const costs = moved("expense");
    const total = (lines: { amount: Amount }[]) => lines.reduce((sum, line) => sum + line.amount.minor, 0);
    const mine = exports.filter((e) => e.debitTotal.currency === currency);
    const sentThrough = mine.length ? mine.map((e) => e.dateTo).sort().at(-1)! : null;
    const start = sentThrough ? shift(sentThrough, 1) : closedThrough;
    const nextExport = closedThrough && start && start <= closedThrough ? { dateFrom: start, dateTo: closedThrough } : null;
    const end = monthEnd(month);
    return {
      currency,
      month,
      dateFrom: `${month}-01`,
      dateTo: end < today ? end : today,
      accounts,
      income: {
        income,
        costs,
        incomeTotal: amount(total(income)),
        costTotal: amount(total(costs)),
        net: amount(total(income) - total(costs)),
      },
      closedThrough,
      sentThrough,
      nextExport,
      exports: mine,
    };
  }

  return {
    getBooks(currency, month) {
      if (month > today.slice(0, 7)) return Promise.reject(new BooksError("month_in_future"));
      return Promise.resolve(books(currency, month));
    },
    exportJournal(currency, days: Days) {
      if (days.dateTo < days.dateFrom) return Promise.reject(new BooksError("days_backwards"));
      if (!closedThrough || days.dateTo > closedThrough) return Promise.reject(new BooksError("day_not_closed"));
      exports.unshift({
        id: `stub-export-${exports.length + 1}`,
        ...days,
        lines: 12,
        debitTotal: { minor: currency === "UGX" ? 198_400_000 : 0, currency },
        exportedBy: reader.id,
        exportedByName: reader.name,
        exportedAt: new Date().toISOString(),
      });
      const content = "Date,Journal,Account code,Account name,Kind,Currency,Debit,Credit\r\n";
      return Promise.resolve({ file: new Blob([content], { type: "text/csv" }), filename: journalFilename(currency, days) });
    },
  };
}
