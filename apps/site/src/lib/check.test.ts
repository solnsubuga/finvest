import { describe, expect, it } from "vitest";
import { describeChecked, normaliseCode, parseCheck, showCode } from "./check.ts";

describe("normaliseCode", () => {
  it("reads a code as typed, ignoring case, spaces and dashes", () => {
    for (const typed of ["K7Q2-9XMA-4TPD", "k7q2 9xma 4tpd", " K7Q29XMA4TPD ", "k7q2.9xma.4tpd"]) {
      expect(normaliseCode(typed)).toBe("K7Q29XMA4TPD");
    }
  });

  it("reads letters that look like digits as the digits, as the API does", () => {
    expect(normaliseCode("OOOO-IIII-LLLL")).toBe("000011111111");
  });

  it("refuses anything that cannot be a code", () => {
    for (const typed of ["", "K7Q2-9XMA", "K7Q2-9XMA-4TPDX", "K7Q2-9XMA-4TP!", "UUUUUUUUUUUU", "A".repeat(41)]) {
      expect(normaliseCode(typed)).toBeNull();
    }
  });

  it("shows a code in three groups", () => {
    expect(showCode("K7Q29XMA4TPD")).toBe("K7Q2-9XMA-4TPD");
  });
});

const genuine = {
  result: "genuine",
  statement: {
    kind: "requested",
    period_start: "2026-10-01",
    period_end: "2026-10-31",
    issued_at: "2026-11-13T21:30:00Z",
    account_ending: "4827",
    closing: [{ minor: 12_500_000, currency: "UGX" }],
  },
  retry_after_seconds: null,
};

describe("parseCheck", () => {
  it("reads a genuine statement, with the day it was issued in Kampala", () => {
    const answer = parseCheck(genuine);
    expect(answer?.result).toBe("genuine");
    if (answer?.result !== "genuine") return;
    // 21:30 UTC on the 13th is past midnight in Kampala.
    expect(answer.statement.issuedOn).toBe("2026-11-14");
    expect(describeChecked(answer.statement)).toEqual([
      ["Statement", "Statement the client asked for"],
      ["Period", "1 October 2026 to 31 October 2026"],
      ["Issued", "14 November 2026"],
      ["Account number ends", "4827"],
      ["Value at the end", "UGX 12,500,000"],
    ]);
  });

  it("names the currency of each value when there are two", () => {
    const answer = parseCheck({
      ...genuine,
      statement: {
        ...genuine.statement,
        kind: "monthly",
        closing: [
          { minor: 100, currency: "UGX" },
          { minor: 1050, currency: "USD" },
        ],
      },
    });
    if (answer?.result !== "genuine") throw new Error("expected genuine");
    expect(describeChecked(answer.statement)).toContainEqual(["Value at the end, USD", "$10.50"]);
    expect(describeChecked(answer.statement)[0]).toEqual(["Statement", "Monthly statement"]);
  });

  it("reads not found and too many tries", () => {
    expect(parseCheck({ result: "not_found", statement: null })).toEqual({ result: "not_found" });
    expect(parseCheck({ result: "too_many_tries", retry_after_seconds: 120 })).toEqual({
      result: "too_many_tries",
      retryAfterSeconds: 120,
    });
  });

  it("refuses an answer it does not understand rather than calling it genuine", () => {
    for (const body of [
      null,
      "genuine",
      { result: "genuine" },
      { ...genuine, statement: { ...genuine.statement, account_ending: "48270" } },
      { ...genuine, statement: { ...genuine.statement, closing: [] } },
      { ...genuine, statement: { ...genuine.statement, closing: [{ minor: 1.5, currency: "UGX" }] } },
      { ...genuine, statement: { ...genuine.statement, period_end: "31 October" } },
    ]) {
      expect(parseCheck(body)).toBeNull();
    }
  });
});
