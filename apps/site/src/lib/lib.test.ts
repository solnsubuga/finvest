import { describe, expect, it } from "vitest";
import { fallback } from "../data/fallback.ts";
import { estimate, rateHundredths } from "./estimate.ts";
import {
  addDays,
  formatDay,
  formatMoney,
  formatWhole,
  parseWhole,
  todayInKampala,
} from "./format.ts";
import { parseCatalogue, parseQuote } from "./products.ts";

const answer = () => ({
  updated_on: "2026-12-01",
  products: [
    {
      code: "TREA-91",
      name: "91 Day Treasury Bill",
      kind: "treasury_bill",
      currency: "UGX",
      term_days: 91,
      minimum_minor: 100000,
      interest_paid: "at_maturity",
      status: "live",
      client_rate_percent: "10.20",
    },
    {
      code: "USD-BOND",
      name: "USD Fixed Income",
      kind: "fixed_income",
      currency: "USD",
      term_days: null,
      minimum_minor: 50000,
      interest_paid: "monthly",
      status: "paused",
      client_rate_percent: "6.89",
    },
  ],
});

describe("parseCatalogue", () => {
  it("reads a good answer, paused products included", () => {
    const catalogue = parseCatalogue(answer());
    expect(catalogue?.updatedOn).toBe("2026-12-01");
    expect(catalogue?.products.map((p) => [p.code, p.status])).toEqual([
      ["TREA-91", "live"],
      ["USD-BOND", "paused"],
    ]);
    expect(catalogue?.products[0]?.clientRatePercent).toBe("10.20");
  });

  it("carries nothing but the fields the site shows", () => {
    const value = answer();
    Object.assign(value.products[0] as object, { annual_rate_bps: 1250, tax_bps: 1500 });
    const product = parseCatalogue(value)?.products[0];
    expect(Object.keys(product ?? {}).sort()).toEqual(
      [
        "clientRatePercent",
        "code",
        "currency",
        "interestPaid",
        "kind",
        "minimumMinor",
        "name",
        "status",
        "termDays",
      ].sort(),
    );
  });

  it.each([
    ["not an object", () => "rates"],
    ["no products", () => ({ ...answer(), products: [] })],
    ["a bad date", () => ({ ...answer(), updated_on: "yesterday" })],
    ["a rate as a number", () => withProduct({ client_rate_percent: 10.2 })],
    ["a rate with one decimal", () => withProduct({ client_rate_percent: "10.2" })],
    ["markup in a rate", () => withProduct({ client_rate_percent: "<b>99.99</b>" })],
    ["a fractional minimum", () => withProduct({ minimum_minor: 100000.5 })],
    ["a minimum as text", () => withProduct({ minimum_minor: "100000" })],
    ["an unknown currency", () => withProduct({ currency: "KES" })],
    ["a term of zero", () => withProduct({ term_days: 0 })],
    ["an empty name", () => withProduct({ name: " " })],
    ["the same code twice", () => ({ ...answer(), products: [answer().products[0], answer().products[0]] })],
    ["a draft product", () => withProduct({ status: "draft" })],
    ["no status", () => withProduct({ status: undefined })],
  ])("refuses %s", (_, build) => {
    expect(parseCatalogue(build())).toBeNull();
  });

  function withProduct(change: Record<string, unknown>) {
    const value = answer();
    return { ...value, products: [{ ...value.products[0], ...change }] };
  }
});

describe("parseQuote", () => {
  const quote = () => ({
    product: "TREA-91",
    amount_minor: 1_000_000,
    currency: "UGX",
    starts_on: "2026-11-13",
    matures_on: "2027-02-12",
    interest_minor: 26_490,
    total_minor: 1_026_490,
  });

  it("reads the exact figure", () => {
    expect(parseQuote(quote(), "TREA-91", 1_000_000n)).toEqual({
      product: "TREA-91",
      amountMinor: 1_000_000n,
      currency: "UGX",
      maturesOn: "2027-02-12",
      interestMinor: 26_490n,
      totalMinor: 1_026_490n,
    });
  });

  it.each([
    ["an answer for another amount", () => quote(), "TREA-91", 2_000_000n],
    ["an answer for another product", () => quote(), "TREA-182", 1_000_000n],
    ["figures that do not add up", () => ({ ...quote(), total_minor: 1_030_000 }), "TREA-91", 1_000_000n],
    ["an open term with no figures", () => ({ ...quote(), interest_minor: null, total_minor: null, matures_on: null }), "TREA-91", 1_000_000n],
    ["a fractional figure", () => ({ ...quote(), interest_minor: 26_490.5 }), "TREA-91", 1_000_000n],
    ["an error body", () => ({ code: "validation_failed" }), "TREA-91", 1_000_000n],
  ])("refuses %s", (_, build, product, amount) => {
    expect(parseQuote(build(), product, amount)).toBeNull();
  });
});

describe("the built-in rates", () => {
  it("pass the same check as an answer from the API", () => {
    const asAnswer = {
      updated_on: fallback.updatedOn,
      products: fallback.products.map((p) => ({
        code: p.code,
        name: p.name,
        kind: p.kind,
        currency: p.currency,
        term_days: p.termDays,
        minimum_minor: p.minimumMinor,
        interest_paid: p.interestPaid,
        client_rate_percent: p.clientRatePercent,
        status: p.status,
      })),
    };
    expect(parseCatalogue(asAnswer)).toEqual(fallback);
  });
});

describe("estimate", () => {
  it("matches the design canvas example", () => {
    expect(estimate(1_000_000n, "10.63", 91)).toEqual({ interest: 26_502n, total: 1_026_502n });
  });

  it("rounds down", () => {
    // 100,000 x 11.69% x 182 / 365 = 5,828.99...
    expect(estimate(100_000n, "11.69", 182)?.interest).toBe(5_828n);
  });

  it("stays exact for amounts too large for floating point", () => {
    expect(estimate(9_999_999_999_999n, "10.63", 182)?.interest).toBe(530_043_835_616n);
  });

  it("refuses nonsense", () => {
    expect(estimate(0n, "10.63", 91)).toBeNull();
    expect(estimate(1000n, "ten", 91)).toBeNull();
    expect(estimate(1000n, "10.63", 0)).toBeNull();
    expect(rateHundredths("10.6")).toBeNull();
  });
});

describe("figures", () => {
  it("writes money without floating point", () => {
    expect(formatMoney(100_000, "UGX")).toBe("UGX 100,000");
    expect(formatMoney(1_026_502n, "UGX")).toBe("UGX 1,026,502");
    expect(formatMoney(50_000, "USD")).toBe("$500");
    expect(formatMoney(125_050, "USD")).toBe("$1,250.50");
    expect(formatWhole(1_000_000n)).toBe("1,000,000");
  });

  it("reads a typed amount", () => {
    expect(parseWhole("1,000,000")).toBe(1_000_000n);
    expect(parseWhole(" 250 000 ")).toBe(250_000n);
    expect(parseWhole("")).toBeNull();
    expect(parseWhole("1.5")).toBeNull();
    expect(parseWhole("-5")).toBeNull();
    expect(parseWhole("12345678901234")).toBeNull();
  });

  it("works out dates", () => {
    expect(addDays("2026-11-13", 91)).toBe("2027-02-12");
    expect(addDays("2027-12-31", 182)).toBe("2028-06-30");
    expect(formatDay("2027-02-12")).toBe("12 February 2027");
    expect(todayInKampala(new Date("2026-11-13T21:30:00Z"))).toBe("2026-11-14");
  });
});
