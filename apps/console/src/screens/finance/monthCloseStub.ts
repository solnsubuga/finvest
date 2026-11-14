/**
 * Sample month closes for `pnpm dev` and preview builds, checked the way the API checks them.
 * Nothing here is real: the people are the stub team and the books are made up, in minor units.
 */

import { MonthCloseError, type BookLine, type MonthClose, type MonthCloseApi, type MonthCloses } from "./monthClose.ts";

interface Me {
  id: string;
  name: string;
}

export interface MonthCloseStubOptions {
  /** No month closed yet, and nothing waiting. */
  empty?: boolean;
  /** Why the next month cannot be proposed, such as `days_not_closed`. */
  refusal?: string;
  /** The last day closed in the ledger. */
  closedThrough?: string;
  now?: () => number;
}

const BOOKS: BookLine[] = [
  { currency: "UGX", code: "bank.trust", kind: "asset", opening: 1_204_500_000, debits: 386_250_000, credits: 341_900_000, closing: 1_248_850_000 },
  { currency: "UGX", code: "iotec.wallet", kind: "asset", opening: 42_300_000, debits: 118_400_000, credits: 121_750_000, closing: 38_950_000 },
  { currency: "UGX", code: "client.cash", kind: "liability", opening: -96_800_000, debits: 402_100_000, credits: 406_350_000, closing: -101_050_000 },
  { currency: "UGX", code: "client.invested", kind: "liability", opening: -1_150_000_000, debits: 61_000_000, credits: 94_000_000, closing: -1_183_000_000 },
  { currency: "UGX", code: "tax.withheld", kind: "liability", opening: 0, debits: 0, credits: 3_750_000, closing: -3_750_000 },
  { currency: "USD", code: "bank.trust", kind: "asset", opening: 4_820_000, debits: 610_000, credits: 225_000, closing: 5_205_000 },
  { currency: "USD", code: "client.invested", kind: "liability", opening: -4_820_000, debits: 225_000, credits: 610_000, closing: -5_205_000 },
];

const DIGEST = "9f2c4be17a0d5e3386c1f04b2a7e9d15c8b3a6f0e4d2917c5b8a3e6f1d0c4b27";

export function createStubMonthCloseApi(me: Me, options: MonthCloseStubOptions = {}): MonthCloseApi {
  const now = options.now ?? Date.now;
  const start = now();
  const hoursAgo = (hours: number) => new Date(start - hours * 3_600_000).toISOString();
  const closes: MonthClose[] = options.empty
    ? []
    : [
        { id: "-NstubMonthClose0002", month: "2026-10", status: "proposed", books: BOOKS, booksSha256: DIGEST, reason: "Every October day reconciled and closed", proposedBy: "stub-finance", proposedByName: "Daniel Opio", proposedAt: hoursAgo(5), decidedBy: null, decidedByName: null, decidedAt: null, decisionReason: null, yours: me.id === "stub-finance" },
        { id: "-NstubMonthClose0001", month: "2026-09", status: "approved", books: BOOKS, booksSha256: DIGEST.split("").reverse().join(""), reason: "September reconciled", proposedBy: "stub-finance", proposedByName: "Daniel Opio", proposedAt: hoursAgo(990), decidedBy: "stub-admin", decidedByName: "Solomon N.", decidedAt: hoursAgo(985), decisionReason: "Checked against the bank statement", yours: me.id === "stub-finance" },
      ];
  const answered = new Map<string, MonthClose>();
  const refused = (reason: string) => new MonthCloseError("refused", reason);

  function lastClosed(): string | null {
    return closes.filter((close) => close.status === "approved").map((close) => close.month).sort().at(-1) ?? null;
  }

  function nextMonth(): string {
    const last = lastClosed();
    if (!last) return "2026-10";
    const [year, month] = last.split("-").map(Number) as [number, number];
    return month === 12 ? `${year + 1}-01` : `${year}-${String(month + 1).padStart(2, "0")}`;
  }

  function refusal(): string | null {
    if (closes.some((close) => close.status === "proposed")) return "close_already_proposed";
    if (options.refusal) return options.refusal;
    // The stub's today is in November 2026, so November has not ended.
    return nextMonth() >= "2026-11" ? "month_not_ended" : null;
  }

  return {
    async getCloses(): Promise<MonthCloses> {
      return {
        lastClosed: lastClosed(),
        next: { month: nextMonth(), closedThrough: options.closedThrough ?? "2026-11-13", refusal: refusal() },
        closes: closes.map((close) => ({ ...close })),
      };
    },
    async propose(month, reason, idempotencyKey) {
      const earlier = answered.get(idempotencyKey);
      if (earlier) return { ...earlier };
      if (!reason.trim()) throw refused("reason_required");
      const blocked = refusal();
      if (blocked) throw refused(blocked);
      if (month !== nextMonth()) throw refused("close_months_in_order");
      const close: MonthClose = {
        id: `-NstubMonthClose${String(closes.length + 1).padStart(4, "0")}`,
        month,
        status: "proposed",
        books: BOOKS,
        booksSha256: DIGEST,
        reason: reason.trim(),
        proposedBy: me.id,
        proposedByName: me.name,
        proposedAt: new Date(now()).toISOString(),
        decidedBy: null,
        decidedByName: null,
        decidedAt: null,
        decisionReason: null,
        yours: true,
      };
      closes.unshift(close);
      answered.set(idempotencyKey, close);
      return { ...close };
    },
    async decide(id, decision, reason, idempotencyKey) {
      const earlier = decision === "approve" ? answered.get(idempotencyKey) : undefined;
      if (earlier) return { ...earlier };
      const close = closes.find((found) => found.id === id);
      if (!close) throw new MonthCloseError("not_found");
      if (!reason.trim()) throw refused("reason_required");
      if (close.status !== "proposed") throw refused("not_waiting");
      if (decision === "withdraw" && !close.yours) throw refused("not_proposed_by_you");
      if (decision !== "withdraw" && close.yours) throw refused("proposed_by_you");
      Object.assign(close, {
        status: decision === "approve" ? "approved" : decision === "decline" ? "declined" : "withdrawn",
        decidedBy: me.id,
        decidedByName: me.name,
        decidedAt: new Date(now()).toISOString(),
        decisionReason: reason.trim(),
      });
      if (decision === "approve") answered.set(idempotencyKey, close);
      return { ...close };
    },
  };
}
