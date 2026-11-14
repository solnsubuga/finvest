import type { ClientInvestmentOut, PaymentView, PayoutOut } from "@finvest/api-client/client";

import { ActivityItem, ActivityStatus } from "./types";

/** The parts of the API's answers the list is made from. Amounts are integers in the smallest unit. */
export type PaymentRow = Pick<PaymentView, "id" | "investment_id" | "method" | "status" | "amount" | "created_at" | "confirmed_at">;
export type InvestmentRow = Pick<
  ClientInvestmentOut,
  "id" | "product_name" | "currency" | "status" | "maturity_date" | "interest_at_maturity_minor"
>;
export type PayoutRow = Pick<PayoutOut, "id" | "amount" | "kind" | "status" | "created_at">;

const METHOD: Record<PaymentRow["method"], string> = {
  mtn: "MTN Mobile Money",
  airtel: "Airtel Money",
  bank_transfer: "Bank transfer",
};

const PAYMENT_STATUS: Record<PaymentRow["status"], [ActivityStatus, string]> = {
  created: ["under_way", "Starting"],
  sending: ["under_way", "Starting"],
  awaiting_approval: ["under_way", "Waiting for you to approve"],
  uncertain: ["under_way", "Checking with your network"],
  in_review: ["under_way", "Being checked"],
  confirmed: ["done", "Paid"],
  failed: ["failed", "Did not go through"],
};

const PAYOUT_STATUS: Record<PayoutRow["status"], [ActivityStatus, string]> = {
  in_review: ["under_way", "Being checked"],
  processing: ["under_way", "On the way"],
  paid: ["done", "Paid to you"],
  failed: ["failed", "Did not go through"],
  cancelled: ["failed", "Cancelled"],
};

/** Interest is paid at the end of the maturity day, Kampala time. */
function endOfDay(isoDate: string): string {
  return `${isoDate}T23:59:59+03:00`;
}

/**
 * One list from the three reads, newest first. Interest is a row only once an
 * investment has matured and earned some: before that it is still being earned.
 */
export function buildActivity(
  payments: PaymentRow[],
  investments: InvestmentRow[],
  payouts: PayoutRow[],
): ActivityItem[] {
  const names = new Map(investments.map((investment) => [investment.id, investment.product_name]));
  const items: ActivityItem[] = [];

  for (const payment of payments) {
    const [status, statusLabel] = PAYMENT_STATUS[payment.status];
    const name = names.get(payment.investment_id);
    items.push({
      key: `payment:${payment.id}`,
      kind: "payment",
      at: payment.confirmed_at ?? payment.created_at,
      title: name ? `Paid for ${name}` : "Paid for an investment",
      detail: METHOD[payment.method],
      amount: payment.amount,
      direction: "out",
      status,
      statusLabel,
      investmentId: payment.investment_id,
    });
  }

  for (const investment of investments) {
    const interest = investment.interest_at_maturity_minor;
    if (investment.status !== "matured" || !investment.maturity_date || !interest || interest <= 0) {
      continue;
    }
    items.push({
      key: `interest:${investment.id}`,
      kind: "interest",
      at: endOfDay(investment.maturity_date),
      title: `Interest on ${investment.product_name}`,
      detail: "Earned at maturity, after tax",
      amount: { currency: investment.currency, minor: interest },
      direction: "in",
      status: "done",
      statusLabel: "Earned",
      investmentId: investment.id,
    });
  }

  for (const payout of payouts) {
    const [status, statusLabel] = PAYOUT_STATUS[payout.status];
    items.push({
      key: `payout:${payout.id}`,
      kind: "payout",
      at: payout.created_at,
      title: "Payout",
      detail: payout.kind === "bank" ? "To your bank account" : "To your mobile money",
      amount: payout.amount,
      direction: "in",
      status,
      statusLabel,
      investmentId: null,
    });
  }

  return items.sort((a, b) => Date.parse(b.at) - Date.parse(a.at) || a.key.localeCompare(b.key));
}

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const KAMPALA_OFFSET_MS = 3 * 60 * 60 * 1000;

/** "November 2026", the Kampala month a row falls in. */
export function monthHeading(iso: string): string {
  const at = new Date(Date.parse(iso) + KAMPALA_OFFSET_MS);
  return `${MONTHS[at.getUTCMonth()]} ${at.getUTCFullYear()}`;
}

/** "13 Nov", the Kampala day a row falls in. */
export function shortDay(iso: string): string {
  const at = new Date(Date.parse(iso) + KAMPALA_OFFSET_MS);
  return `${at.getUTCDate()} ${(MONTHS[at.getUTCMonth()] as string).slice(0, 3)}`;
}

/** Rows grouped under their month, keeping the newest-first order. */
export function byMonth(items: ActivityItem[]): { month: string; items: ActivityItem[] }[] {
  const groups: { month: string; items: ActivityItem[] }[] = [];
  for (const item of items) {
    const month = monthHeading(item.at);
    const last = groups[groups.length - 1];
    if (last && last.month === month) {
      last.items.push(item);
    } else {
      groups.push({ month, items: [item] });
    }
  }
  return groups;
}
