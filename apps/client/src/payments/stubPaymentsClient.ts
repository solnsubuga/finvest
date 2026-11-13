import {
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
 */
export const DEMO_PAYABLE_ID = "demo-91-day-bill";

const DEMO_PAYABLE: Payable = {
  id: DEMO_PAYABLE_ID,
  kind: "investment",
  title: "91 Day Treasury Bill",
  amount: { currency: "UGX", minor: 1_000_000 },
};

/** How long each demo step lasts. The real ones are set by the network and the client. */
const SENDING_MS = 1_500;
const OUTCOME_MS = 7_000;
const CHECKING_MS = 14_000;

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

type Stored = { request: MobileMoneyRequest; id: string; requestedAtMs: number; scenario: string };

export type StubOptions = {
  /** Artificial delay so loading states are visible. Tests pass 0. */
  delayMs?: number;
  /** The clock, in milliseconds. Tests move it by hand. */
  now?: () => number;
  /** Chooses the outcome for a paying number. The default is its last two digits. */
  scenarioFor?: (payer: string) => string;
};

export function createStubPaymentsClient(options: StubOptions = {}): PaymentsClient {
  const delayMs = options.delayMs ?? 450;
  const now = options.now ?? Date.now;
  const scenarioFor = options.scenarioFor ?? ((payer: string) => payer.slice(-2));
  const deposits = new Map<string, Stored>();
  const byKey = new Map<string, string>();
  let counter = 0;

  const pause = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();

  function read(stored: Stored): Deposit {
    const elapsed = now() - stored.requestedAtMs;
    const outcome = outcomeFor(stored.scenario, elapsed);
    const confirmed = outcome.status === "confirmed";
    const confirmedAfter = stored.scenario === "04" ? CHECKING_MS : OUTCOME_MS;
    return {
      id: stored.id,
      payable: DEMO_PAYABLE,
      amount: DEMO_PAYABLE.amount,
      network: stored.request.network,
      payer: stored.request.payer,
      status: outcome.status,
      failure: outcome.failure,
      requestedAt: new Date(stored.requestedAtMs).toISOString(),
      confirmedAt: confirmed ? new Date(stored.requestedAtMs + confirmedAfter).toISOString() : null,
      networkReference: confirmed ? `DEMO-${stored.id.toUpperCase()}` : null,
      reference: `FV-DEMO-${stored.id}`,
    };
  }

  return {
    async getPayable(payableId) {
      await pause();
      if (payableId !== DEMO_PAYABLE_ID) {
        throw new PaymentError("not_payable");
      }
      return DEMO_PAYABLE;
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
      if (request.payableId !== DEMO_PAYABLE_ID) {
        throw new PaymentError("not_payable");
      }
      // One thing is paid for once. Only a failed payment frees it for another try.
      for (const stored of deposits.values()) {
        const status = read(stored).status;
        if (status === "confirmed") {
          throw new PaymentError("not_payable");
        }
        if (status !== "failed") {
          throw new PaymentError("already_paying");
        }
      }
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
