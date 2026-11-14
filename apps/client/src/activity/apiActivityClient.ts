import type { PaymentPage } from "@finvest/api-client/client";

import { api } from "@/api/http";

import { buildActivity, PaymentRow } from "./build";
import { ActivityClient, ActivityError } from "./types";

/** Pages of payments read at most. A client with more sees the newest of them. */
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
        return buildActivity(payments, investments, payouts);
      } catch {
        throw new ActivityError();
      }
    },
  };
}
