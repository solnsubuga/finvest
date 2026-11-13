import type { ClientInvestmentOut, ProductOut, QuoteOut } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";

import { Amount, Currency, Investment, InvestingClient, InvestingError, Product, Quote } from "./types";

/**
 * The investing screens' contract, answered by the real API (`/client/products`,
 * `/client/investments`, backend/app/modules/investments/api.py). Every rate is
 * after tax and every amount is net, in whole numbers of the smallest unit. They
 * are passed through untouched: the app works nothing out.
 */

function money(minor: number, currency: Currency): Amount {
  return { currency, minor };
}

export function toProduct(view: ProductOut): Product {
  return {
    code: view.code,
    name: view.name,
    kind: view.kind,
    currency: view.currency,
    termDays: view.term_days,
    minimum: money(view.minimum_minor, view.currency),
    interestPaid: view.interest_paid,
    // A client is never shown a draft. If one arrives, it takes no investments, like a paused one.
    status: view.status === "live" ? "live" : "paused",
    ratePercent: view.client_rate_percent,
  };
}

export function toQuote(view: QuoteOut): Quote {
  return {
    productCode: view.product,
    amount: money(view.amount_minor, view.currency),
    startsOn: view.starts_on,
    maturesOn: view.matures_on,
    interest: view.interest_minor === null ? null : money(view.interest_minor, view.currency),
    total: view.total_minor === null ? null : money(view.total_minor, view.currency),
  };
}

export function toInvestment(view: ClientInvestmentOut): Investment {
  const currency = view.currency;
  return {
    id: view.id,
    productCode: view.product_code,
    productName: view.product_name,
    status: view.status,
    amount: money(view.amount_minor, currency),
    ratePercent: view.client_rate_percent,
    termDays: view.term_days,
    atMaturity: view.at_maturity,
    canChangeMaturity: view.can_change_maturity,
    payBy: view.pay_by,
    startDate: view.start_date,
    maturityDate: view.maturity_date,
    daysEarned: view.days_earned,
    interestSoFar: money(view.interest_so_far_minor, currency),
    interestToday: money(view.interest_today_minor, currency),
    value: money(view.value_minor, currency),
    interestAtMaturity: view.interest_at_maturity_minor === null ? null : money(view.interest_at_maturity_minor, currency),
    totalAtMaturity: view.total_at_maturity_minor === null ? null : money(view.total_at_maturity_minor, currency),
    rolledIntoId: view.rolled_into_id,
    payoutId: view.payout_id,
    waitingForDestination: view.waiting_for_destination,
  };
}

/**
 * Turns what the API or the network said into a reason an investing screen can show.
 *
 * Only a definite refusal gets its own reason. Anything whose outcome is unknown (offline,
 * a timeout, a server error) is "unavailable", which the review screen treats as "may have
 * been saved": it keeps the same key and the same choices, so trying again cannot invest twice.
 */
export function toInvestingError(error: unknown): InvestingError {
  if (error instanceof InvestingError) return error;
  if (!(error instanceof ApiError) || error.uncertain) return new InvestingError("unavailable");
  switch (error.code) {
    case "not_found":
      return new InvestingError("not_found");
    case "idempotency_key_reused":
      return new InvestingError("key_reused");
    case "forbidden":
      // Nobody invests before their ID check is approved, and that is the only refusal a
      // signed-in client gets here.
      return new InvestingError("not_verified");
    case "conflict":
      // The most investments waiting for payment at once.
      return new InvestingError("too_many_unpaid");
    case "validation_failed": {
      const codes = error.fields.map((field) => field.code);
      if (codes.includes("below_minimum")) return new InvestingError("below_minimum");
      if (codes.includes("product_not_open")) return new InvestingError("product_not_open");
      return new InvestingError("unavailable");
    }
    default:
      return new InvestingError("unavailable");
  }
}

async function wrap<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toInvestingError(error);
  }
}

export function createApiInvestingClient(): InvestingClient {
  return {
    listProducts: () => wrap(async () => (await api("GET /client/products", {})).map(toProduct)),

    quote: (productCode, amount) =>
      wrap(async () => {
        const view = await api("GET /client/products/{code}/quote", {
          params: { code: productCode },
          query: { amount_minor: amount.minor },
        });
        // The product's currency decides the quote's. An amount in another one was never asked for.
        if (view.currency !== amount.currency) throw new InvestingError("unavailable");
        return toQuote(view);
      }),

    requestInvestment: (request) =>
      wrap(async () =>
        toInvestment(
          await api("POST /client/investments", {
            body: {
              product_code: request.productCode,
              amount_minor: request.amount.minor,
              currency: request.amount.currency,
              at_maturity: request.atMaturity,
            },
            idempotencyKey: request.idempotencyKey,
          }),
        ),
      ),

    // Newest first and without cancelled ones, as the API sends them.
    listInvestments: () => wrap(async () => (await api("GET /client/investments", {})).map(toInvestment)),

    getInvestment: (investmentId) =>
      wrap(async () =>
        toInvestment(await api("GET /client/investments/{investment_id}", { params: { investment_id: investmentId } })),
      ),
  };
}
