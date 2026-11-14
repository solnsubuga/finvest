import { describe, expect, it } from "vitest";
import type { BookableProduct } from "./api.ts";
import { csvCells, readSheet, TEMPLATE } from "./sheet.ts";

const PRODUCTS: BookableProduct[] = [
  { code: "TREA-91", name: "91-day Treasury bill", currency: "UGX", termDays: 91, minimum: { minor: 100_000, currency: "UGX" } },
  { code: "SAVE-OPEN", name: "Open savings", currency: "UGX", termDays: null, minimum: { minor: 50_000, currency: "UGX" } },
];

describe("investments: a sheet of bookings", () => {
  it("splits quoted cells with commas, quotes and line breaks", () => {
    expect(csvCells('a,"b, c","say ""hi""\nthere"\r\n1,2,3\n\n')).toEqual([
      ["a", "b, c", 'say "hi"\nthere'],
      ["1", "2", "3"],
    ]);
  });

  it("reads each row against today's products, rolling over when blank", () => {
    const sheet = readSheet(
      "\uFEFFAccount_Number,product_code,amount,at_maturity\n1001 004827,trea-91,\"1,000,000\",\n1001005130,TREA-91,500000,pay_interest\n1001003318,SAVE-OPEN,75000,\n",
      PRODUCTS,
    );
    if (!sheet.ok) throw new Error("not read");
    expect(sheet.rows.map((r) => [r.line, r.accountNumber, r.product?.code, r.amount?.minor, r.atMaturity, r.problems])).toEqual([
      [2, "1001004827", "TREA-91", 1_000_000, "roll_over", []],
      [3, "1001005130", "TREA-91", 500_000, "pay_interest", []],
      [4, "1001003318", "SAVE-OPEN", 75_000, null, []],
    ]);
  });

  it("names every problem a row has", () => {
    const sheet = readSheet(
      "account_number,product_code,amount,at_maturity\n,NOPE,1,\n1001004827,TREA-91,1.5,sometimes\n1001004827,TREA-91,50000,\n1001003318,SAVE-OPEN,75000,pay_out\n1001003318,SAVE-OPEN,75000,pay_out\n",
      PRODUCTS,
    );
    if (!sheet.ok) throw new Error("not read");
    expect(sheet.rows.map((r) => r.problems)).toEqual([
      ["no_account_number", "unknown_product"],
      ["not_an_amount", "unknown_choice"],
      ["below_minimum"],
      ["no_choice_for_open_term"],
      ["no_choice_for_open_term", "repeated"],
    ]);
  });

  it("refuses a file without the columns, without rows, or with too many", () => {
    expect(readSheet("client,amount\n1,2\n", PRODUCTS)).toEqual({ ok: false, error: "missing_columns", missing: ["account_number", "product_code"] });
    expect(readSheet("account_number,product_code,amount\n", PRODUCTS)).toEqual({ ok: false, error: "empty" });
    expect(readSheet("", PRODUCTS)).toEqual({ ok: false, error: "empty" });
    const many = "account_number,product_code,amount\n" + "1001004827,TREA-91,100000\n".repeat(201);
    expect(readSheet(many, PRODUCTS)).toMatchObject({ ok: false, error: "too_many_rows" });
  });

  it("reads its own template", () => {
    expect(readSheet(TEMPLATE, PRODUCTS)).toMatchObject({ ok: true, rows: [{ problems: [] }] });
  });
});
