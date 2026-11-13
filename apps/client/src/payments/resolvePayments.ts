import { PaymentError, PaymentsClient } from "./types";

type StubModule = typeof import("./stubPaymentsClient");

export type ResolvedPayments = {
  client: PaymentsClient;
  /** Something to pay for in the demo, so the screens can be tried. Null outside the demo. */
  demoPayableId: string | null;
};

/**
 * The in-memory demo confirms payments that never happened, so it must never
 * be what a real client reaches. It follows the demo sign-in: loaded in
 * development, and in a production build only when EXPO_PUBLIC_DEMO_AUTH=1 was
 * set on purpose at build time.
 *
 * The condition is written inline, and the stub is required inside it, so the
 * bundler can see it is false in a normal production build and leave the stub
 * out of the bundle altogether. CI checks that it did.
 */
function loadStub(): StubModule | null {
  if (__DEV__ || process.env.EXPO_PUBLIC_DEMO_AUTH === "1") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubPaymentsClient") as StubModule;
  }
  return null;
}

/** Chooses what the pay screens talk to. Without the stub, every payment is refused. */
export function resolvePaymentsClient(stub: StubModule | null = loadStub()): ResolvedPayments {
  if (stub) {
    return { client: stub.createStubPaymentsClient(), demoPayableId: stub.DEMO_PAYABLE_ID };
  }
  return { client: unavailablePaymentsClient, demoPayableId: null };
}

const refuse = async (): Promise<never> => {
  throw new PaymentError("unavailable");
};

const unavailablePaymentsClient: PaymentsClient = {
  getPayable: refuse,
  payWithMobileMoney: refuse,
  getDeposit: refuse,
  getBankInstructions: refuse,
  submitBankReceipt: refuse,
  getBankTransfer: refuse,
};
