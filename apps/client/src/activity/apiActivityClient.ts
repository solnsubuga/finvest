import type { PaymentPage } from "@finvest/api-client/client";

import { api } from "@/api/http";

import { buildActivity, from, PaymentRow } from "./build";
import { ActivityClient, ActivityError } from "./types";

/** Pages of payments read at most. A client with more sees everything since the oldest one read. */
const MAX_PAYMENT_PAGES = 4;

/** The Activity list from `GET /client/payments`, `/client/investments` and `/client/payouts`. */
export function createApiActivityClient(): ActivityClient {
  return {
    async list() {
      try {
        const payments: PaymentRow[] = [];
        let before: string | null = null;
        for (let page = 0; page < MAX_PAYMENT_PAGES; page += 1) {
          const answer: PaymentPage = await api("GET /client/payments", {
            query: before ? { before } : {},
          });
          payments.push(...answer.items);
          before = answer.next_before;
          if (!before) break;
        }
        const [investments, payouts] = await Promise.all([
          api("GET /client/investments", {}),
          api("GET /client/payouts", {}),
        ]);
        // Payments come newest first, so the last one read is where a cut list starts.
        const since = before ? (payments[payments.length - 1]?.created_at ?? null) : null;
        return { items: from(buildActivity(payments, investments, payouts), since), since };
      } catch {
        throw new ActivityError();
      }
    },
  };
}
