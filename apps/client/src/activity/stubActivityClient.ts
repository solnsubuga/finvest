import { buildActivity, from, InvestmentRow, PaymentRow, PayoutRow } from "./build";
import { ActivityClient, ActivityError } from "./types";

/**
 * An in-memory stand-in for the three reads behind Activity. Nothing here is
 * real. `ActivityProvider.tsx` keeps it out of production builds.
 *
 * The two demo accounts have a few weeks of history. An account made by
 * signing up has none, which shows the empty state. A number ending in 99
 * cannot reach the service. The second demo account stands for a client with
 * more payments than the app reads, so its list starts 60 days back.
 */

const SEEDED = new Set(["+256772123456", "+256701000200"]);
const CUT_SHORT = "+256701000200";
const DAY_MS = 24 * 60 * 60 * 1000;

function ago(now: number, days: number, hours = 0): string {
  return new Date(now - days * DAY_MS - hours * 60 * 60 * 1000).toISOString();
}

function day(now: number, days: number): string {
  return new Date(now - days * DAY_MS + 3 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

function history(now: number): [PaymentRow[], InvestmentRow[], PayoutRow[]] {
  const ugx = (minor: number) => ({ currency: "UGX" as const, minor });
  const investments: InvestmentRow[] = [
    { id: "demo-activity-inv-1", product_name: "91 day Treasury bill", currency: "UGX", status: "active", maturity_date: day(now, -88), interest_at_maturity_minor: 61_800 },
    { id: "demo-activity-inv-2", product_name: "182 day Treasury bill", currency: "UGX", status: "active", maturity_date: day(now, -170), interest_at_maturity_minor: 128_400 },
    { id: "demo-activity-inv-3", product_name: "91 day Treasury bill", currency: "UGX", status: "matured", maturity_date: day(now, 18), interest_at_maturity_minor: 15_400 },
    { id: "demo-activity-inv-4", product_name: "Fixed deposit, 6 months", currency: "UGX", status: "awaiting_payment", maturity_date: null, interest_at_maturity_minor: null },
  ];
  const payments: PaymentRow[] = [
    { id: "demo-activity-pay-1", investment_id: "demo-activity-inv-4", method: "airtel", status: "failed", amount: ugx(500_000), created_at: ago(now, 0, 2), confirmed_at: null },
    { id: "demo-activity-pay-2", investment_id: "demo-activity-inv-1", method: "mtn", status: "confirmed", amount: ugx(3_000_000), created_at: ago(now, 3, 1), confirmed_at: ago(now, 3) },
    { id: "demo-activity-pay-3", investment_id: "demo-activity-inv-2", method: "bank_transfer", status: "in_review", amount: ugx(4_250_000), created_at: ago(now, 1, 5), confirmed_at: null },
    { id: "demo-activity-pay-4", investment_id: "demo-activity-inv-3", method: "mtn", status: "confirmed", amount: ugx(1_000_000), created_at: ago(now, 110), confirmed_at: ago(now, 110) },
  ];
  const payouts: PayoutRow[] = [
    { id: "demo-activity-payout-1", amount: ugx(1_015_400), kind: "mobile_money", status: "paid", created_at: ago(now, 17, 20) },
  ];
  return [payments, investments, payouts];
}

export function createStubActivityClient(
  phone: string | null,
  { delayMs = 450, now = Date.now }: { delayMs?: number; now?: () => number } = {},
): ActivityClient {
  return {
    async list() {
      if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
      if (phone?.endsWith("99")) throw new ActivityError();
      if (!phone || !SEEDED.has(phone)) return { items: [], since: null };
      const items = buildActivity(...history(now()));
      if (phone !== CUT_SHORT) return { items, since: null };
      const since = ago(now(), 60);
      return { items: from(items, since), since };
    },
  };
}
