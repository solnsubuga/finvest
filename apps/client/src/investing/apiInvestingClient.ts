import type { ClientInvestmentOut, ClientTransferOut, DestinationOut, ProductOut, QuoteOut } from "@finvest/api-client/client";

import { api, ApiError, isAccountClosing } from "@/api/http";
import { banksOrNone, nameOf } from "@/payouts/banks";
import { accountLabel } from "@/payouts/format";
import type { Bank } from "@/payouts/types";

import { Amount, Currency, Investment, InvestingClient, InvestingError, PayoutAccount, Product, Quote, Transfer } from "./types";

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
    payoutDestinationId: view.payout_destination_id,
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
    canWithdraw: view.can_withdraw,
    withdrawalRequestedAt: view.withdrawal_requested_at,
    canSend: view.can_send ?? false,
  };
}

export function toTransfer(view: ClientTransferOut): Transfer {
  return {
    id: view.id,
    direction: view.direction === "received" ? "received" : "sent",
    otherName: view.other_name,
    status: view.status,
    amount: money(view.amount_minor, view.currency),
    whole: view.whole,
    message: view.message,
    requestedAt: view.requested_at,
    finishedAt: view.finished_at,
    investmentId: view.investment_id,
    keptInvestmentId: view.kept_investment_id,
    failureCode: view.failure_code,
  };
}

/** An approved account, named the way the client knows it. The number arrives masked. */
export function toPayoutAccount(view: DestinationOut, nowMs: number, banks: Bank[] = []): PayoutAccount {
  return {
    id: view.id,
    kind: view.kind,
    label: accountLabel({
      kind: view.kind,
      network: view.network,
      bankName: view.kind === "bank" ? nameOf(banks, view.bank_code) : null,
      account: view.account,
    }),
    payableFrom: view.payable_from,
    ready: view.payable_from !== null && Date.parse(view.payable_from) <= nowMs,
  };
}

/**
 * Turns what the API or the network said into a reason an investing screen can show.
 *
 * Only a definite refusal gets its own reason. Anything whose outcome is unknown (offline,
 * a timeout, a server error) is "unavailable", which the review screen treats as "may have
 * been saved": it keeps the same key and the same choices, so trying again cannot invest twice.
 */
/** Only a phone with a PIN sends a confirmation; a browser leaves it out. */
const pinOf = (request: { pinConfirmation?: string | null }) =>
  request.pinConfirmation ? { pin_confirmation: request.pinConfirmation } : {};

/** Which call failed, where the same refusal means different things. */
type Call = "withdraw" | "maturity" | "pay_out" | "send" | "cancel" | "lookup" | "other";

const CONFLICT: Record<Call, InvestingError["code"]> = {
  withdraw: "cannot_withdraw",
  maturity: "cannot_change_maturity",
  pay_out: "already_sent",
  send: "cannot_send",
  cancel: "cannot_cancel",
  lookup: "unavailable",
  other: "too_many_unpaid",
};

/** The refusals of a send or a look-up, by the code the API puts on the field. */
const SEND_REFUSALS: Record<string, InvestingError["code"]> = {
  no_such_client: "no_such_client",
  not_an_account_number: "not_an_account_number",
  to_yourself: "to_yourself",
  below_product_minimum: "below_product_minimum",
  more_than_investment_value: "more_than_value",
  remainder_below_product_minimum: "remainder_below_minimum",
  currency_not_the_investments: "invalid",
  matures_today: "cannot_send",
};

export function toInvestingError(error: unknown, call: Call = "other"): InvestingError {
  if (error instanceof InvestingError) return error;
  if (!(error instanceof ApiError) || error.uncertain) return new InvestingError("unavailable");
  switch (error.code) {
    case "not_found":
      // A look-up that found nobody approved with that number.
      return new InvestingError(call === "lookup" ? "no_such_client" : "not_found");
    case "pin_required":
      return new InvestingError("pin_required");
    case "idempotency_key_reused":
      return new InvestingError("key_reused");
    case "rate_limited":
      return new InvestingError(call === "lookup" ? "too_many_lookups" : "unavailable");
    case "forbidden":
      if (error.fields.some((field) => field.code === "restricted_after_code_sign_in")) {
        return new InvestingError("restricted");
      }
      // Nobody invests before their ID check is approved, and that is the only refusal a
      // signed-in client gets here.
      return new InvestingError("not_verified");
    case "conflict":
      if (isAccountClosing(error)) return new InvestingError("account_closing");
      // Withdrawing: not running, already asked for, too close to maturity, or switched off.
      // Investing: the most investments waiting for payment at once.
      // Changing the maturity choice: too close to maturity. Paying out: already sent or rolled over.
      return new InvestingError(CONFLICT[call]);
    case "validation_failed": {
      const codes = error.fields.map((field) => field.code);
      const refusal = codes.map((code) => SEND_REFUSALS[code]).find((found) => found !== undefined);
      if ((call === "send" || call === "lookup") && refusal) return new InvestingError(refusal);
      if (
        codes.includes("not_usable") ||
        codes.includes("destination_in_safety_wait") ||
        codes.includes("destination_not_approved") ||
        codes.includes("destination_not_known_yet")
      ) {
        return new InvestingError("account_not_ready");
      }
      if (codes.includes("over_mobile_money_limit")) return new InvestingError("over_mobile_money_limit");
      if (codes.includes("over_daily_limit") || codes.includes("over_bank_limit")) return new InvestingError("over_limit");
      if (codes.includes("below_minimum")) return new InvestingError("below_minimum");
      if (codes.includes("product_not_open")) return new InvestingError("product_not_open");
      // Still a definite refusal: nothing was saved, so the next try may take a new key.
      return new InvestingError("invalid");
    }
    default:
      return new InvestingError("unavailable");
  }
}

async function wrap<T>(work: () => Promise<T>, call: Call = "other"): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toInvestingError(error, call);
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

    // Only approved accounts can be paid. One still in its safety wait is listed, so the screen
    // can say when it will be ready.
    listPayoutAccounts: () =>
      wrap(async () => {
        const [views, banks] = await Promise.all([api("GET /client/payout-destinations", {}), banksOrNone()]);
        const nowMs = Date.now();
        return views.filter((view) => view.status === "approved").map((view) => toPayoutAccount(view, nowMs, banks));
      }),

    withdrawEarly: (request) =>
      wrap(
        async () =>
          toInvestment(
            await api("POST /client/investments/{investment_id}/withdraw", {
              params: { investment_id: request.investmentId },
              body: { destination_id: request.destinationId, ...pinOf(request) },
              idempotencyKey: request.idempotencyKey,
            }),
          ),
        "withdraw",
      ),

    chooseAtMaturity: (choice) =>
      wrap(
        async () =>
          toInvestment(
            await api("PUT /client/investments/{investment_id}/at-maturity", {
              params: { investment_id: choice.investmentId },
              body: { at_maturity: choice.atMaturity, destination_id: choice.destinationId },
            }),
          ),
        "maturity",
      ),

    payOutMatured: (request) =>
      wrap(
        async () =>
          toInvestment(
            await api("POST /client/investments/{investment_id}/pay-out", {
              params: { investment_id: request.investmentId },
              body: { destination_id: request.destinationId, ...pinOf(request) },
            }),
          ),
        "pay_out",
      ),

    findRecipient: (to) =>
      wrap(async () => {
        const view = await api("GET /client/transfer-recipients", { query: { to } });
        return { accountNumber: view.account_number, name: view.name };
      }, "lookup"),

    sendInvestment: (request) =>
      wrap(
        async () =>
          toTransfer(
            await api("POST /client/investments/{investment_id}/send", {
              params: { investment_id: request.investmentId },
              body: {
                to_account_number: request.toAccountNumber,
                amount_minor: request.amount.minor,
                currency: request.amount.currency,
                message: request.message,
                whole: request.whole,
              },
              idempotencyKey: request.idempotencyKey,
            }),
          ),
        "send",
      ),

    listTransfers: () => wrap(async () => (await api("GET /client/transfers", {})).map(toTransfer)),

    getTransfer: (transferId) =>
      wrap(async () => toTransfer(await api("GET /client/transfers/{transfer_id}", { params: { transfer_id: transferId } }))),

    cancelTransfer: (transferId) =>
      wrap(
        async () =>
          toTransfer(await api("POST /client/transfers/{transfer_id}/cancel", { params: { transfer_id: transferId } })),
        "cancel",
      ),
  };
}
