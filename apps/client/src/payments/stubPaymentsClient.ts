import { addDemoPayable, demoPayable, markDemoPaid } from "@/demo/payables";

import { MAX_RECEIPT_BYTES } from "./receipt";
import {
  BankReceiptSubmission,
  BankTransfer,
  CardOffer,
  CardPayment,
  CardPaymentRequest,
  Deposit,
  DepositStatus,
  FailureReason,
  MobileMoneyRequest,
  Payable,
  PaymentError,
  PaymentsClient,
} from "./types";

/**
 * An in-memory stand-in for the payments API, so the pay screens can be built
 * and tested before the backend routes exist. Nothing here is real: no
 * network, no money, and everything is forgotten when the app reloads.
 * `resolvePayments.ts` keeps it out of production builds.
 *
 * Like the backend's sandbox provider, the last two digits of the paying
 * number choose what happens. The paying number is the account's own, so to
 * see another outcome in the demo, sign up with a number that ends that way:
 *
 *   01  declined on the phone
 *   02  not enough money
 *   03  the prompt is never approved
 *   04  no clear answer at first, so the payment is checked, then confirmed
 *   any other number  approved after a few seconds
 *
 * A bank transfer is matched after a few seconds too, where real staff take
 * hours. The last two digits of the transaction number on the receipt choose:
 *
 *   01  the amount differs
 *   02  not on the bank statement
 *   anything else  matched
 */
export const DEMO_PAYABLE_ID = "demo-91-day-bill";

const DEMO_PAYABLE: Payable = {
  id: DEMO_PAYABLE_ID,
  kind: "investment",
  title: "91 Day Treasury Bill",
  amount: { currency: "UGX", minor: 1_000_000 },
};

/**
 * Card payments in the demo. Pesapal's page is stood in for by the app's own return page, so a
 * card "pays" as soon as the client comes back. These payables show the other outcomes:
 */
export const DEMO_CARD_DECLINED_ID = "demo-card-declined";
export const DEMO_CARD_LIMIT_ID = "demo-card-limit";
export const DEMO_CARDS_PAUSED_ID = "demo-cards-paused";
/** As the backend's default Settings: 3.5% on top, at most UGX 5,000,000 a card payment. */
const DEMO_CARD_BASIS_POINTS = 350;
const DEMO_CARD_MAX = 5_000_000;

const DEMO_CARD_PAYABLES: Record<string, Payable> = {
  [DEMO_CARD_DECLINED_ID]: { ...DEMO_PAYABLE_BASE(), id: DEMO_CARD_DECLINED_ID },
  [DEMO_CARD_LIMIT_ID]: {
    ...DEMO_PAYABLE_BASE(),
    id: DEMO_CARD_LIMIT_ID,
    amount: { currency: "UGX", minor: 8_000_000 },
  },
  [DEMO_CARDS_PAUSED_ID]: { ...DEMO_PAYABLE_BASE(), id: DEMO_CARDS_PAUSED_ID },
};

function DEMO_PAYABLE_BASE(): Payable {
  return {
    id: "",
    kind: "investment",
    title: "91 Day Treasury Bill",
    amount: { currency: "UGX", minor: 1_000_000 },
  };
}

/** The card charge in whole shillings, rounded up, as the backend works it out. */
export function demoCardCharge(minor: number): number {
  return Math.ceil((minor * DEMO_CARD_BASIS_POINTS) / 10_000);
}

/**
 * Other demos can add something to pay for, such as the investment a goal
 * saving makes. They share one store with the demo investing.
 */
export { addDemoPayable };

/** How long each demo step lasts. The real ones are set by the network and the client. */
const SENDING_MS = 1_500;
const OUTCOME_MS = 7_000;
const CHECKING_MS = 14_000;

/** The fixed demo payable, or one the demo investing has saved. */
function payableFor(payableId: string): Payable | null {
  if (payableId === DEMO_PAYABLE_ID) return DEMO_PAYABLE;
  return DEMO_CARD_PAYABLES[payableId] ?? demoPayable(payableId);
}

type StoredCard = {
  request: CardPaymentRequest;
  id: string;
  requestedAtMs: number;
  returnedAtMs: number | null;
};

type Outcome = { status: DepositStatus; failure: FailureReason | null };

function outcomeFor(scenario: string, elapsedMs: number): Outcome {
  if (elapsedMs < SENDING_MS) {
    return { status: "sending", failure: null };
  }
  if (scenario === "04") {
    return elapsedMs < CHECKING_MS
      ? { status: "checking", failure: null }
      : { status: "confirmed", failure: null };
  }
  if (elapsedMs < OUTCOME_MS) {
    return { status: "awaiting_approval", failure: null };
  }
  if (scenario === "01") {
    return { status: "failed", failure: "declined" };
  }
  if (scenario === "02") {
    return { status: "failed", failure: "insufficient_funds" };
  }
  if (scenario === "03") {
    return { status: "failed", failure: "approval_timed_out" };
  }
  return { status: "confirmed", failure: null };
}

/** How long the demo's "staff" take to match a receipt. */
const MATCHING_MS = 9_000;
/** Obviously not a real account. The real details come from the server. */
const DEMO_BANK = {
  bankName: "Demo Bank",
  accountName: "Finvest Demo Client Account",
  accountNumber: "0000 0000 0000",
  branch: "Demo branch",
  reference: "1001004827",
};

/** The paying number as the API shows it again: "+256 77• ••• •56". */
export function maskPhone(e164: string): string {
  return `${e164.slice(0, 4)} ${e164.slice(4, 6)}• ••• •${e164.slice(-2)}`;
}

type StoredTransfer = { submission: BankReceiptSubmission; id: string; submittedAtMs: number };

type Stored = { request: MobileMoneyRequest; id: string; requestedAtMs: number; scenario: string };

export type StubOptions = {
  /** Artificial delay so loading states are visible. Tests pass 0. */
  delayMs?: number;
  /** The clock, in milliseconds. Tests move it by hand. */
  now?: () => number;
  /** Chooses the outcome for a paying number. The default is its last two digits. */
  scenarioFor?: (payer: string) => string;
};

function sameSubmission(a: BankReceiptSubmission, b: BankReceiptSubmission): boolean {
  return (
    a.payableId === b.payableId &&
    a.file.name === b.file.name &&
    a.file.sizeBytes === b.file.sizeBytes &&
    a.file.mimeType === b.file.mimeType &&
    a.amountSent.currency === b.amountSent.currency &&
    a.amountSent.minor === b.amountSent.minor &&
    a.sentOn === b.sentOn &&
    a.bankName === b.bankName &&
    a.transactionNumber === b.transactionNumber
  );
}

export function createStubPaymentsClient(options: StubOptions = {}): PaymentsClient {
  const delayMs = options.delayMs ?? 450;
  const now = options.now ?? Date.now;
  const scenarioFor = options.scenarioFor ?? ((payer: string) => payer.slice(-2));
  const deposits = new Map<string, Stored>();
  const byKey = new Map<string, string>();
  const transfers = new Map<string, StoredTransfer>();
  const transferByKey = new Map<string, string>();
  const cardPayments = new Map<string, StoredCard>();
  const cardByKey = new Map<string, string>();
  let counter = 0;

  function cardOffer(payable: Payable): CardOffer {
    const charge = { currency: payable.amount.currency, minor: demoCardCharge(payable.amount.minor) };
    const total = { currency: payable.amount.currency, minor: payable.amount.minor + charge.minor };
    return {
      amount: payable.amount,
      charge,
      total,
      maxPayment: { currency: "UGX", minor: DEMO_CARD_MAX },
      refused:
        payable.id === DEMO_CARDS_PAUSED_ID
          ? "channel_paused"
          : total.minor > DEMO_CARD_MAX
            ? "above_card_payment_limit"
            : null,
    };
  }

  function readCard(stored: StoredCard): CardPayment {
    const payable = payableFor(stored.request.payableId) ?? DEMO_PAYABLE;
    const offer = cardOffer(payable);
    const declined = payable.id === DEMO_CARD_DECLINED_ID;
    const sinceAsked = now() - stored.requestedAtMs;
    const sinceBack = stored.returnedAtMs === null ? null : now() - stored.returnedAtMs;
    let status: CardPayment["status"] = "sending";
    if (sinceAsked >= SENDING_MS) status = "awaiting_card";
    if (sinceBack !== null && sinceBack >= SENDING_MS * 2) status = declined ? "failed" : "confirmed";
    const confirmedAt =
      status === "confirmed" && stored.returnedAtMs !== null
        ? new Date(stored.returnedAtMs + SENDING_MS * 2).toISOString()
        : null;
    if (confirmedAt) markDemoPaid(payable.id, confirmedAt);
    return {
      id: stored.id,
      payable,
      amount: payable.amount,
      charge: offer.charge,
      total: offer.total,
      status,
      failure: status === "failed" ? "declined" : null,
      pageUrl: status === "awaiting_card" ? `/pay/card/return?payment=${stored.id}&sandbox=1` : null,
      card: status === "confirmed" ? { brand: "Visa", lastFour: "0010" } : null,
      requestedAt: new Date(stored.requestedAtMs).toISOString(),
      confirmedAt,
      reference: `FV-DEMO-${stored.id}`,
    };
  }

  const pause = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();

  function read(stored: Stored): Deposit {
    const elapsed = now() - stored.requestedAtMs;
    const outcome = outcomeFor(stored.scenario, elapsed);
    const confirmed = outcome.status === "confirmed";
    const confirmedAfter = stored.scenario === "04" ? CHECKING_MS : OUTCOME_MS;
    const payable = payableFor(stored.request.payableId) ?? DEMO_PAYABLE;
    const confirmedAt = confirmed ? new Date(stored.requestedAtMs + confirmedAfter).toISOString() : null;
    if (confirmedAt) {
      markDemoPaid(payable.id, confirmedAt);
    }
    return {
      id: stored.id,
      payable,
      amount: payable.amount,
      network: stored.request.network,
      payer: maskPhone(stored.request.payer),
      status: outcome.status,
      failure: outcome.failure,
      requestedAt: new Date(stored.requestedAtMs).toISOString(),
      confirmedAt,
      networkReference: confirmed ? `DEMO-${stored.id.toUpperCase()}` : null,
      reference: `FV-DEMO-${stored.id}`,
    };
  }

  function readTransfer(stored: StoredTransfer): BankTransfer {
    const scenario = stored.submission.transactionNumber.slice(-2);
    const matched = now() - stored.submittedAtMs >= MATCHING_MS;
    const reason = scenario === "01" ? "amount_differs" : scenario === "02" ? "not_on_statement" : null;
    const payable = payableFor(stored.submission.payableId) ?? DEMO_PAYABLE;
    if (matched && !reason) {
      markDemoPaid(payable.id, new Date(stored.submittedAtMs + MATCHING_MS).toISOString());
    }
    return {
      id: stored.id,
      payable,
      amountSent: stored.submission.amountSent,
      status: !matched ? "checking" : reason ? "not_matched" : "confirmed",
      reason: matched ? reason : null,
      submittedAt: new Date(stored.submittedAtMs).toISOString(),
      confirmedAt:
        matched && !reason ? new Date(stored.submittedAtMs + MATCHING_MS).toISOString() : null,
      reference: DEMO_BANK.reference,
    };
  }

  /** One thing is paid for once, by whichever method. Only a failed try frees it. */
  function refuseIfTaken(payableId: string) {
    for (const stored of deposits.values()) {
      if (stored.request.payableId !== payableId) {
        continue;
      }
      const status = read(stored).status;
      if (status === "confirmed") {
        throw new PaymentError("not_payable");
      }
      if (status !== "failed") {
        throw new PaymentError("already_paying", { id: stored.id, method: "mobile_money" });
      }
    }
    for (const stored of cardPayments.values()) {
      if (stored.request.payableId !== payableId) continue;
      const status = readCard(stored).status;
      if (status === "confirmed") throw new PaymentError("not_payable");
      if (status !== "failed") throw new PaymentError("already_paying", { id: stored.id, method: "card" });
    }
    for (const stored of transfers.values()) {
      if (stored.submission.payableId !== payableId) {
        continue;
      }
      const status = readTransfer(stored).status;
      if (status === "confirmed") {
        throw new PaymentError("not_payable");
      }
      if (status === "checking") {
        throw new PaymentError("already_paying", { id: stored.id, method: "bank_transfer" });
      }
    }
  }

  return {
    async getPayable(payableId) {
      await pause();
      const payable = payableFor(payableId);
      if (!payable) {
        throw new PaymentError("not_payable");
      }
      return payable;
    },

    async payWithMobileMoney(request) {
      await pause();
      const known = byKey.get(request.idempotencyKey);
      if (known) {
        const first = deposits.get(known) as Stored;
        // The same key is the same request. With different details it is refused, as the
        // backend refuses it.
        if (
          first.request.payableId !== request.payableId ||
          first.request.network !== request.network ||
          first.request.payer !== request.payer
        ) {
          throw new PaymentError("key_reused");
        }
        return read(first);
      }
      if (!payableFor(request.payableId)) {
        throw new PaymentError("not_payable");
      }
      refuseIfTaken(request.payableId);
      counter += 1;
      const stored: Stored = {
        request,
        id: `d${counter}`,
        requestedAtMs: now(),
        scenario: scenarioFor(request.payer),
      };
      deposits.set(stored.id, stored);
      byKey.set(request.idempotencyKey, stored.id);
      return read(stored);
    },

    async getBankInstructions(payableId) {
      await pause();
      const payable = payableFor(payableId);
      if (!payable) {
        throw new PaymentError("not_payable");
      }
      return { payable, ...DEMO_BANK };
    },

    async submitBankReceipt(submission) {
      await pause();
      const known = transferByKey.get(submission.idempotencyKey);
      if (known) {
        const first = transfers.get(known) as StoredTransfer;
        // The backend refuses a key sent again with a different body, so the stub does too.
        if (!sameSubmission(first.submission, submission)) {
          throw new PaymentError("key_reused");
        }
        return readTransfer(first);
      }
      if (!payableFor(submission.payableId)) {
        throw new PaymentError("not_payable");
      }
      if (submission.file.sizeBytes > MAX_RECEIPT_BYTES) {
        throw new PaymentError("receipt_not_accepted");
      }
      refuseIfTaken(submission.payableId);
      counter += 1;
      const stored: StoredTransfer = { submission, id: `b${counter}`, submittedAtMs: now() };
      transfers.set(stored.id, stored);
      transferByKey.set(submission.idempotencyKey, stored.id);
      return readTransfer(stored);
    },

    async getBankTransfer(transferId) {
      await pause();
      const stored = transfers.get(transferId);
      if (!stored) {
        throw new PaymentError("not_found");
      }
      return readTransfer(stored);
    },

    async getCardOffer(payableId) {
      await pause();
      const payable = payableFor(payableId);
      if (!payable) throw new PaymentError("not_payable");
      return cardOffer(payable);
    },

    async payWithCard(request) {
      await pause();
      const known = cardByKey.get(request.idempotencyKey);
      if (known) {
        const first = cardPayments.get(known) as StoredCard;
        if (first.request.payableId !== request.payableId) throw new PaymentError("key_reused");
        return readCard(first);
      }
      const payable = payableFor(request.payableId);
      if (!payable) throw new PaymentError("not_payable");
      if (cardOffer(payable).refused) throw new PaymentError("card_refused");
      refuseIfTaken(request.payableId);
      counter += 1;
      const stored: StoredCard = { request, id: `c${counter}`, requestedAtMs: now(), returnedAtMs: null };
      cardPayments.set(stored.id, stored);
      cardByKey.set(request.idempotencyKey, stored.id);
      return readCard(stored);
    },

    async getCardPayment(paymentId) {
      await pause();
      const stored = cardPayments.get(paymentId);
      if (!stored) throw new PaymentError("not_found");
      return readCard(stored);
    },

    async cardReturned(paymentId) {
      await pause();
      const stored = cardPayments.get(paymentId);
      if (!stored) throw new PaymentError("not_found");
      if (stored.returnedAtMs === null) stored.returnedAtMs = now();
      return readCard(stored);
    },

    async getDeposit(depositId) {
      await pause();
      const stored = deposits.get(depositId);
      if (!stored) {
        throw new PaymentError("not_found");
      }
      return read(stored);
    },
  };
}
