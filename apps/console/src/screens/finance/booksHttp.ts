import type { Http } from "@/api/http.ts";
import type { Amount, Currency } from "./api.ts";
import { toBooksError, type BookLine, type Books, type BooksApi, type Days, type FormatOption, type JournalFormat } from "./books.ts";

interface DaysJson {
  date_from: string;
  date_to: string;
}

interface BooksJson {
  currency: Currency;
  month: string;
  date_from: string;
  date_to: string;
  accounts: BookLine[];
  income: {
    income: { code: string; name: string; amount: Amount }[];
    costs: { code: string; name: string; amount: Amount }[];
    income_total: Amount;
    cost_total: Amount;
    net: Amount;
  };
  closed_through: string | null;
  sent_through: string | null;
  next_export: DaysJson | null;
  exports: (DaysJson & {
    id: string;
    lines: number;
    debit_total: Amount;
    exported_by: string;
    exported_by_name: string | null;
    exported_at: string;
    format: JournalFormat;
  })[];
  formats: FormatOption[];
}

export function toBooks(json: BooksJson): Books {
  return {
    currency: json.currency,
    month: json.month,
    dateFrom: json.date_from,
    dateTo: json.date_to,
    accounts: json.accounts.map((a) => ({
      code: a.code,
      name: a.name,
      kind: a.kind,
      opening: a.opening,
      debits: a.debits,
      credits: a.credits,
      closing: a.closing,
    })),
    income: {
      income: json.income.income,
      costs: json.income.costs,
      incomeTotal: json.income.income_total,
      costTotal: json.income.cost_total,
      net: json.income.net,
    },
    closedThrough: json.closed_through,
    sentThrough: json.sent_through,
    nextExport: json.next_export ? { dateFrom: json.next_export.date_from, dateTo: json.next_export.date_to } : null,
    exports: json.exports.map((e) => ({
      id: e.id,
      dateFrom: e.date_from,
      dateTo: e.date_to,
      lines: e.lines,
      debitTotal: e.debit_total,
      exportedBy: e.exported_by,
      exportedByName: e.exported_by_name,
      exportedAt: e.exported_at,
      format: e.format,
    })),
    formats: json.formats.map((f) => ({ format: f.format, name: f.name, ready: f.ready, unmapped: f.unmapped, currencies: f.currencies })),
  };
}

/** The server's own name for the file: `finvest-journal-xero-ugx-2026-11-12.csv`. */
export function journalFilename(currency: Currency, { dateFrom, dateTo }: Days, format: JournalFormat = "csv"): string {
  const system = format === "csv" ? "" : `-${format}`;
  return `finvest-journal${system}-${currency.toLowerCase()}-${dateFrom}${dateFrom === dateTo ? "" : `-to-${dateTo}`}.csv`;
}

export function createHttpBooksApi(http: Http): BooksApi {
  async function call<T>(work: () => Promise<T>): Promise<T> {
    try {
      return await work();
    } catch (error) {
      throw toBooksError(error);
    }
  }
  const currencyQuery = (currency: Currency) => `currency=${encodeURIComponent(currency)}`;
  return {
    getBooks: (currency, month) =>
      call(async () =>
        toBooks(await http.request<BooksJson>("GET", `/staff/finance?${currencyQuery(currency)}&month=${encodeURIComponent(month)}`)),
      ),
    // A POST: the server records every export as it answers.
    exportJournal: (currency, days, format) =>
      call(async () => ({
        file: await http.download("/staff/finance/journal.csv", {
          method: "POST",
          body: { currency, date_from: days.dateFrom, date_to: days.dateTo, format },
        }),
        filename: journalFilename(currency, days, format),
      })),
  };
}
