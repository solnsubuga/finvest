import { Platform } from "react-native";
import type {
  BankTransferDetails,
  BondHoldingOut,
  CardOffer as CardOfferView,
  ClientInvestmentOut,
  PaymentView,
  Upload,
} from "@finvest/api-client/client";

import { api, ApiError, isAccountClosing } from "@/api/http";
import { bondPayableId, holdingIdOf } from "@/bonds/types";

import { shortReference } from "./reference";
import {
  BankTransfer,
  CardOffer,
  CardPayment,
  CardPaymentStatus,
  CardRefusal,
  Deposit,
  DepositStatus,
  FailureReason,
  MismatchReason,
  Payable,
  PaymentError,
  PaymentsClient,
  ReceiptFile,
} from "./types";

/**
 * The pay screens' contract, answered by the real API (backend/app/modules/payments).
 *
 * The amount is never sent for mobile money: the API takes the investment's own amount, and
 * collects from the number on the client's account, from their session. Every request that
 * can move money carries the screen's idempotency key, so a retry after a lost answer asks
 * for the money once.
 */

const FAILURES = new Set<FailureReason>([
  "declined",
  "insufficient_funds",
  "approval_timed_out",
  "account_not_found",
  "limit_exceeded",
]);

const MISMATCHES = new Set<MismatchReason>([
  "amount_differs",
  "not_on_statement",
  "unreadable",
  "name_differs",
]);

/** A provider's refusal in its own words, or "other" for our codes, such as an amount mismatch. */
export function toFailure(code: string | null): FailureReason {
  return code && FAILURES.has(code as FailureReason) ? (code as FailureReason) : "other";
}

export function toMismatch(code: string | null): MismatchReason {
  return code && MISMATCHES.has(code as MismatchReason) ? (code as MismatchReason) : "other";
}

export function toPayable(view: ClientInvestmentOut): Payable {
  return {
    id: view.id,
    kind: "investment",
    title: view.product_name,
    amount: { minor: view.amount_minor, currency: view.currency },
  };
}

/** A bond purchase, paid for like an investment under its own ID. Its amount is the cost. */
export function toBondPayable(view: BondHoldingOut): Payable {
  return {
    id: bondPayableId(view.id),
    kind: "bond",
    title: view.name,
    amount: { minor: view.cost_minor, currency: view.currency },
  };
}

/** `created` is saved but not sent yet; `uncertain` is being asked about, so it reads as checking. */
const DEPOSIT_STATUS: Record<PaymentView["status"], DepositStatus> = {
  created: "sending",
  sending: "sending",
  awaiting_approval: "awaiting_approval",
  uncertain: "checking",
  in_review: "checking",
  confirmed: "confirmed",
  failed: "failed",
};

const CARD_STATUS: Record<PaymentView["status"], CardPaymentStatus> = {
  created: "sending",
  sending: "sending",
  awaiting_approval: "awaiting_card",
  uncertain: "checking",
  in_review: "checking",
  confirmed: "confirmed",
  failed: "failed",
};

const CARD_REFUSALS = new Set<CardRefusal>([
  "channel_paused",
  "cards_unavailable",
  "above_card_payment_limit",
  "above_card_30_day_limit",
]);

export function toCardOffer(view: CardOfferView): CardOffer {
  return {
    amount: view.amount,
    charge: view.charge,
    total: view.total,
    maxPayment: view.max_payment,
    refused:
      view.refused === null
        ? null
        : CARD_REFUSALS.has(view.refused as CardRefusal)
          ? (view.refused as CardRefusal)
          : "cards_unavailable",
  };
}

export function toCardPayment(view: PaymentView, payable: Payable): CardPayment {
  if (view.method !== "card") {
    throw new PaymentError("not_found");
  }
  const zero = { minor: 0, currency: view.amount.currency };
  return {
    id: view.id,
    payable,
    amount: view.amount,
    charge: view.card_charge ?? zero,
    total: view.card_total ?? view.amount,
    status: CARD_STATUS[view.status],
    failure: view.status === "failed" ? toFailure(view.failure) : null,
    pageUrl: view.card_page_url ?? null,
    card: view.card ? { brand: view.card.brand, lastFour: view.card.last_four } : null,
    requestedAt: view.created_at,
    confirmedAt: view.confirmed_at,
    reference: shortReference(view.id),
  };
}

export function toDeposit(view: PaymentView, payable: Payable): Deposit {
  // A card payment is not a mobile money deposit: the card screens read it.
  if (view.method === "bank_transfer" || view.method === "card") {
    throw new PaymentError("not_found");
  }
  return {
    id: view.id,
    payable,
    amount: view.amount,
    network: view.method,
    // Masked by the API, as the client may see it again: "+256 77• ••• •56".
    payer: view.payer ?? "",
    status: DEPOSIT_STATUS[view.status],
    failure: view.status === "failed" ? toFailure(view.failure) : null,
    requestedAt: view.created_at,
    confirmedAt: view.confirmed_at,
    // The API keeps the network's reference to itself. Ours is what support looks up.
    networkReference: null,
    reference: shortReference(view.id),
    onPage: view.on_page ?? false,
    pageUrl: view.page_url ?? null,
  };
}

export function toBankTransfer(view: PaymentView, payable: Payable): BankTransfer {
  if (view.method !== "bank_transfer") {
    throw new PaymentError("not_found");
  }
  return {
    id: view.id,
    payable,
    amountSent: view.amount,
    status: view.status === "confirmed" ? "confirmed" : view.status === "failed" ? "not_matched" : "checking",
    reason: view.status === "failed" ? toMismatch(view.failure) : null,
    submittedAt: view.created_at,
    confirmedAt: view.confirmed_at,
    // Our own short reference for the payment, which support can look up.
    reference: shortReference(view.id),
  };
}

type Call = "pay" | "read";

/** Turns what the API or the network said into a reason a pay screen can show. */
export function toPaymentError(error: unknown, call: Call = "read"): PaymentError {
  if (error instanceof PaymentError) return error;
  if (!(error instanceof ApiError)) return new PaymentError("unavailable");
  switch (error.code) {
    case "not_found":
      return new PaymentError(call === "pay" ? "not_payable" : "not_found");
    case "conflict":
      if (isAccountClosing(error)) return new PaymentError("account_closing");
      return new PaymentError("not_payable");
    case "idempotency_key_reused":
      return new PaymentError("key_reused");
    case "validation_failed": {
      const fields = error.fields.map((problem) => problem.field);
      // Too many prompts to other people's numbers today: the client's own still works.
      if (error.fields.some((problem) => problem.code === "other_number_limit")) {
        return new PaymentError("other_number_limit");
      }
      if (fields.some((field) => field.endsWith("receipt"))) return new PaymentError("receipt_not_accepted");
      // A bank receipt for another amount than the investment's would be refused by staff later.
      if (fields.some((field) => field.endsWith("amount_minor"))) return new PaymentError("amount_differs");
      // Mobile money takes whole shillings only, so a dollar investment is paid by bank.
      if (fields.some((field) => field.endsWith("amount"))) return new PaymentError("amount_not_allowed");
      // A definite refusal: nothing was saved, so it must not read as a lost answer.
      return new PaymentError("not_accepted");
    }
    default:
      return new PaymentError("unavailable");
  }
}

/**
 * On the web the picker hands back a blob: or data: address, which a form cannot read, so the
 * file is fetched into a File first. On a phone, FormData reads `{ uri, name, type }` from disk.
 */
export async function uploadable(file: ReceiptFile): Promise<Upload> {
  if (Platform.OS === "web") {
    const blob = await (await fetch(file.uri)).blob();
    return new File([blob], file.name, { type: file.mimeType });
  }
  return { uri: file.uri, name: file.name, type: file.mimeType };
}

export function createApiPaymentsClient(): PaymentsClient {
  // A payable's title and amount do not change once it can be paid for, so one read serves
  // every status check of its payment.
  const payables = new Map<string, Payable>();

  /** An investment's payable, or a bond purchase's when the ID names one ("bond~<holding>"). */
  async function readPayable(payableId: string): Promise<{ payable: Payable; payableNow: boolean }> {
    const holdingId = holdingIdOf(payableId);
    if (holdingId !== null) {
      const view = await api("GET /client/bond-holdings/{holding_id}", { params: { holding_id: holdingId } });
      return { payable: toBondPayable(view), payableNow: view.status === "unpaid" };
    }
    const view = await api("GET /client/investments/{investment_id}", { params: { investment_id: payableId } });
    return { payable: toPayable(view), payableNow: view.status === "awaiting_payment" };
  }

  async function payableFor(payableId: string): Promise<Payable> {
    const known = payables.get(payableId);
    if (known) return known;
    const { payable } = await readPayable(payableId);
    payables.set(payableId, payable);
    return payable;
  }

  /** What a payment pays for, from the payment itself: an investment, or a bond purchase. */
  function payableIdOf(view: PaymentView): string {
    return view.purpose === "bond" ? bondPayableId(view.investment_id) : view.investment_id;
  }

  function livePayment(payableId: string): Promise<PaymentView> {
    const holdingId = holdingIdOf(payableId);
    return holdingId !== null
      ? api("GET /client/bond-holdings/{holding_id}/payment", { params: { holding_id: holdingId } })
      : api("GET /client/investments/{investment_id}/payment", { params: { investment_id: payableId } });
  }

  /**
   * A 409 means the investment or bond purchase is already paid for, or has a payment under
   * way, or can no longer be paid. Its live payment tells which.
   */
  async function whyRefused(payableId: string, error: unknown): Promise<PaymentError> {
    if (!(error instanceof ApiError) || error.code !== "conflict" || isAccountClosing(error)) {
      return toPaymentError(error, "pay");
    }
    try {
      const live = await livePayment(payableId);
      if (live.status === "confirmed") return new PaymentError("not_payable");
      return new PaymentError("already_paying", {
        id: live.id,
        method: live.method === "bank_transfer" || live.method === "card" ? live.method : "mobile_money",
      });
    } catch {
      return new PaymentError("not_payable");
    }
  }

  async function readPayment(paymentId: string): Promise<{ view: PaymentView; payable: Payable }> {
    const view = await api("GET /client/payments/{payment_id}", { params: { payment_id: paymentId } });
    return { view, payable: await payableFor(payableIdOf(view)) };
  }

  return {
    async getPayable(payableId) {
      try {
        const { payable, payableNow } = await readPayable(payableId);
        if (!payableNow) throw new PaymentError("not_payable");
        payables.set(payable.id, payable);
        return payable;
      } catch (error) {
        throw toPaymentError(error, "pay");
      }
    },

    async mobileMoneyOnPage() {
      try {
        return (await api("GET /client/mobile-money", {})).on_page;
      } catch (error) {
        throw toPaymentError(error, "pay");
      }
    },

    async payWithMobileMoney(request) {
      let view: PaymentView;
      const holdingId = holdingIdOf(request.payableId);
      const body = { network: request.network, phone: request.payer };
      try {
        view =
          holdingId !== null
            ? await api("POST /client/bond-holdings/{holding_id}/payments/mobile-money", {
                params: { holding_id: holdingId },
                body,
                idempotencyKey: request.idempotencyKey,
              })
            : await api("POST /client/investments/{investment_id}/payments/mobile-money", {
                params: { investment_id: request.payableId },
                body,
                idempotencyKey: request.idempotencyKey,
              });
      } catch (error) {
        throw await whyRefused(request.payableId, error);
      }
      try {
        return toDeposit(view, await payableFor(payableIdOf(view)));
      } catch (error) {
        throw toPaymentError(error);
      }
    },

    async getCardOffer(payableId) {
      try {
        const holdingId = holdingIdOf(payableId);
        return toCardOffer(
          holdingId !== null
            ? await api("GET /client/bond-holdings/{holding_id}/payments/card", { params: { holding_id: holdingId } })
            : await api("GET /client/investments/{investment_id}/payments/card", { params: { investment_id: payableId } }),
        );
      } catch (error) {
        throw toPaymentError(error, "pay");
      }
    },

    async payWithCard(request) {
      let view: PaymentView;
      const holdingId = holdingIdOf(request.payableId);
      try {
        view =
          holdingId !== null
            ? await api("POST /client/bond-holdings/{holding_id}/payments/card", {
                params: { holding_id: holdingId },
                idempotencyKey: request.idempotencyKey,
              })
            : await api("POST /client/investments/{investment_id}/payments/card", {
                params: { investment_id: request.payableId },
                idempotencyKey: request.idempotencyKey,
              });
      } catch (error) {
        // Paused or over a limit since the offer was read: the pay screen reads it again.
        if (error instanceof ApiError && (error.status === 503 || error.code === "validation_failed")) {
          throw new PaymentError("card_refused");
        }
        throw await whyRefused(request.payableId, error);
      }
      try {
        return toCardPayment(view, await payableFor(payableIdOf(view)));
      } catch (error) {
        throw toPaymentError(error);
      }
    },

    async getCardPayment(paymentId) {
      try {
        const { view, payable } = await readPayment(paymentId);
        return toCardPayment(view, payable);
      } catch (error) {
        throw toPaymentError(error);
      }
    },

    async cardReturned(paymentId) {
      try {
        const view = await api("POST /client/payments/{payment_id}/card-return", {
          params: { payment_id: paymentId },
        });
        return view.method === "card" ? "card" : "mobile_money";
      } catch (error) {
        throw toPaymentError(error);
      }
    },

    async getDeposit(depositId) {
      try {
        const { view, payable } = await readPayment(depositId);
        return toDeposit(view, payable);
      } catch (error) {
        throw toPaymentError(error);
      }
    },

    async getBankInstructions(payableId) {
      // Bank transfers are not offered for bonds yet (docs/bonds.md).
      if (holdingIdOf(payableId) !== null) throw new PaymentError("not_payable");
      let details: BankTransferDetails;
      try {
        details = await api("GET /client/investments/{investment_id}/bank-transfer-details", {
          params: { investment_id: payableId },
        });
      } catch (error) {
        // No account is set up yet for the investment's currency: the API says so with a 503.
        if (error instanceof ApiError && error.code === "unavailable" && error.status === 503) {
          throw new PaymentError("bank_details_missing");
        }
        throw await whyRefused(payableId, error);
      }
      try {
        const payable = await payableFor(payableId);
        return {
          // The amount to send is the investment's, as the API states it on the details.
          payable: { ...payable, amount: details.amount },
          bankName: details.bank_name,
          accountName: details.account_name,
          accountNumber: details.account_number,
          branch: details.branch,
          reference: details.reference,
        };
      } catch (error) {
        throw toPaymentError(error);
      }
    },

    async submitBankReceipt(submission) {
      if (holdingIdOf(submission.payableId) !== null) throw new PaymentError("not_payable");
      let view: PaymentView;
      try {
        view = await api("POST /client/investments/{investment_id}/payments/bank-transfer", {
          params: { investment_id: submission.payableId },
          body: {
            receipt: await uploadable(submission.file),
            amount_minor: submission.amountSent.minor,
            sent_on: submission.sentOn,
            bank_name: submission.bankName,
            transaction_number: submission.transactionNumber,
          },
          idempotencyKey: submission.idempotencyKey,
        });
      } catch (error) {
        throw await whyRefused(submission.payableId, error);
      }
      try {
        return toBankTransfer(view, await payableFor(payableIdOf(view)));
      } catch (error) {
        throw toPaymentError(error);
      }
    },

    async getBankTransfer(transferId) {
      try {
        const { view, payable } = await readPayment(transferId);
        return toBankTransfer(view, payable);
      } catch (error) {
        throw toPaymentError(error);
      }
    },
  };
}
