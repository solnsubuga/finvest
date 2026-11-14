import { kampalaStamp, matches, toCsv, withinDays } from "../table";
import { ActivityItem } from "../types";

const item = (overrides: Partial<ActivityItem> = {}): ActivityItem => ({
  key: "payment:1",
  kind: "payment",
  at: "2026-11-13T12:32:00Z",
  title: "Payment for 91 Day Treasury Bill",
  detail: "MTN Mobile Money",
  amount: { currency: "UGX", minor: 1_000_000 },
  direction: "out",
  status: "done",
  statusLabel: "Paid",
  investmentId: "inv-1",
  ...overrides,
});

describe("matches", () => {
  it("finds a row by any word of its title, detail or status, whatever the case", () => {
    expect(matches(item(), "treasury")).toBe(true);
    expect(matches(item(), "mtn PAID")).toBe(true);
    expect(matches(item(), "airtel")).toBe(false);
  });

  it("finds a row by its amount with or without commas", () => {
    expect(matches(item(), "1,000,000")).toBe(true);
    expect(matches(item(), "1000000")).toBe(true);
    expect(matches(item(), "UGX 1,000,000")).toBe(true);
    expect(matches(item(), "2000000")).toBe(false);
  });

  it("lets everything through an empty search", () => {
    expect(matches(item(), "   ")).toBe(true);
  });
});

describe("withinDays", () => {
  const now = Date.parse("2026-11-14T12:00:00Z");
  it("keeps the last 30 days and drops anything older", () => {
    expect(withinDays(item({ at: "2026-10-16T12:00:00Z" }), now, 30)).toBe(true);
    expect(withinDays(item({ at: "2026-10-15T11:59:59Z" }), now, 30)).toBe(false);
  });
});

describe("toCsv", () => {
  it("writes Kampala time, whole units with no separator, and who the money went to", () => {
    const csv = toCsv([
      item(),
      item({ key: "payout:2", kind: "payout", title: "Payout", detail: "To your bank account", direction: "in", amount: { currency: "USD", minor: 50_025 }, statusLabel: "Paid to you" }),
    ]);
    expect(csv.split("\r\n")).toEqual([
      "Date (Kampala),What,For,Direction,Amount,Currency,Status",
      "2026-11-13 15:32,Payment for 91 Day Treasury Bill,MTN Mobile Money,From you,1000000,UGX,Paid",
      "2026-11-13 15:32,Payout,To your bank account,To you,500.25,USD,Paid to you",
      "",
    ]);
  });

  it("quotes commas and quotes, and keeps a spreadsheet from reading a field as a formula", () => {
    const line = toCsv([item({ title: 'A "big", one', detail: "=HYPERLINK(1)" })]).split("\r\n")[1];
    expect(line).toBe('2026-11-13 15:32,"A ""big"", one",\'=HYPERLINK(1),From you,1000000,UGX,Paid');
  });

  it("writes only the heading for no rows", () => {
    expect(toCsv([])).toBe("Date (Kampala),What,For,Direction,Amount,Currency,Status\r\n");
  });
});

describe("kampalaStamp", () => {
  it("moves past midnight in Kampala to the next day", () => {
    expect(kampalaStamp("2026-11-13T22:30:00Z")).toBe("2026-11-14 01:30");
  });
});
