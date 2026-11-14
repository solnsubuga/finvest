/**
 * A spreadsheet of investments to book for clients, saved as CSV. Each row becomes one
 * booking, which a second person approves as for one booked by hand, and each client pays for
 * theirs as usual. Nothing here talks to Finvest: the screen looks the clients up and sends the
 * bookings one by one, each with its own key.
 *
 * Columns, with a header row: `account_number`, `product_code`, `amount` in whole units such as
 * `1,000,000`, and optionally `at_maturity` (`roll_over`, `pay_out` or `pay_interest`, rolling
 * over when blank).
 */

import { parseAmount } from "@/screens/dealing-desk/format.ts";
import type { Amount } from "../../products/api.ts";
import type { AtMaturity } from "../api.ts";
import type { BookableProduct } from "./api.ts";

export const MAX_ROWS = 200;
export const TEMPLATE = "account_number,product_code,amount,at_maturity\n1001004827,TREA-91,1000000,roll_over\n";
const COLUMNS = ["account_number", "product_code", "amount", "at_maturity"] as const;
const CHOICES: readonly string[] = ["roll_over", "pay_out", "pay_interest"] satisfies AtMaturity[];

export type RowProblem =
  | "no_account_number"
  | "unknown_product"
  | "not_an_amount"
  | "below_minimum"
  | "unknown_choice"
  | "no_choice_for_open_term"
  | "repeated";

export interface SheetRow {
  /** The line in the file, counting the header as 1, so staff can find it. */
  line: number;
  accountNumber: string;
  productCode: string;
  amountText: string;
  product: BookableProduct | null;
  amount: Amount | null;
  atMaturity: AtMaturity | null;
  problems: RowProblem[];
}

export type SheetError = "empty" | "missing_columns" | "too_many_rows";

export type Sheet = { ok: true; rows: SheetRow[] } | { ok: false; error: SheetError; missing?: string[] };

/** Splits CSV text into cells, with quoted cells that may hold commas, quotes or line breaks. */
export function csvCells(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        cell += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(cell);
      cell = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[i + 1] === "\n") i += 1;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }
  if (cell !== "" || row.length > 0) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((cells) => cells.some((value) => value.trim() !== ""));
}

/** Reads a sheet against the products that take money today. Clients are looked up after. */
export function readSheet(text: string, products: readonly BookableProduct[]): Sheet {
  const [header, ...body] = csvCells(text.replace(/^\uFEFF/, ""));
  if (!header) return { ok: false, error: "empty" };
  const names = header.map((name) => name.trim().toLowerCase());
  const missing = COLUMNS.slice(0, 3).filter((name) => !names.includes(name));
  if (missing.length > 0) return { ok: false, error: "missing_columns", missing };
  if (body.length === 0) return { ok: false, error: "empty" };
  if (body.length > MAX_ROWS) return { ok: false, error: "too_many_rows" };
  const at = (cells: string[], name: string) => (cells[names.indexOf(name)] ?? "").trim();
  const seen = new Set<string>();
  return {
    ok: true,
    rows: body.map((cells, index) => {
      const accountNumber = at(cells, "account_number").replace(/\s/g, "");
      const productCode = at(cells, "product_code").toUpperCase();
      const amountText = at(cells, "amount");
      const choice = names.includes("at_maturity") ? at(cells, "at_maturity").toLowerCase() : "";
      const product = products.find((p) => p.code === productCode) ?? null;
      const amount = product ? parseAmount(amountText, product.currency) : null;
      const problems: RowProblem[] = [];
      if (!accountNumber) problems.push("no_account_number");
      if (!product) problems.push("unknown_product");
      if (product && amount === null) problems.push("not_an_amount");
      if (product && amount && amount.minor < product.minimum.minor) problems.push("below_minimum");
      if (choice && !CHOICES.includes(choice)) problems.push("unknown_choice");
      if (choice && product && product.termDays === null) problems.push("no_choice_for_open_term");
      const same = `${accountNumber}|${productCode}|${amountText}|${choice}`;
      if (seen.has(same)) problems.push("repeated");
      seen.add(same);
      const atMaturity = product && product.termDays !== null ? ((CHOICES.includes(choice) ? choice : "roll_over") as AtMaturity) : null;
      return { line: index + 2, accountNumber, productCode, amountText, product, amount, atMaturity, problems };
    }),
  };
}

const ROW_PROBLEMS: Record<RowProblem, string> = {
  no_account_number: "No account number.",
  unknown_product: "No product with this code takes new money.",
  not_an_amount: "The amount is not a whole number like 1,000,000.",
  below_minimum: "Below the product's minimum.",
  unknown_choice: "At maturity must be roll_over, pay_out or pay_interest.",
  no_choice_for_open_term: "This product has no fixed term, so leave at_maturity blank.",
  repeated: "The same as a row above. Remove one if the client asked once.",
};

export function rowProblemText(problem: RowProblem): string {
  return ROW_PROBLEMS[problem];
}

const SHEET_ERRORS: Record<SheetError, string> = {
  empty: "The file has no rows to book. Keep the header row and add one row per investment.",
  missing_columns: "The file is missing columns. Start from the template.",
  too_many_rows: `Book at most ${MAX_ROWS} investments from one file.`,
};

export function sheetErrorText(sheet: Extract<Sheet, { ok: false }>): string {
  const missing = sheet.missing?.length ? ` Missing: ${sheet.missing.join(", ")}.` : "";
  return SHEET_ERRORS[sheet.error] + missing;
}
