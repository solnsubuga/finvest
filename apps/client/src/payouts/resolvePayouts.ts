import { createApiPayoutsClient } from "./apiPayoutsClient";
import { PayoutsClient } from "./types";

type StubModule = typeof import("./stubPayoutsClient");

/**
 * The in-memory demo invents accounts and payouts, so it must never be what a real client
 * reaches. Like the other demos it is loaded in tests, and in a build only when
 * EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose. Written inline, so the bundler leaves the stub
 * out of a normal production build.
 */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubPayoutsClient") as StubModule;
  }
  return null;
}

/** The address the app was opened at, before signing in moved it. Only the demo reads it. */
const OPENED_WITH = typeof window !== "undefined" ? (window.location?.search ?? "") : "";

const NAME_CHECK_SAMPLES = ["checking", "not_answered", "limit_reached", "waiting_for_id", "no_name"] as const;

function nameCheckSample(search: string): (typeof NAME_CHECK_SAMPLES)[number] | undefined {
  const asked = /namecheck=([a-z_]+)/.exec(search)?.[1];
  return NAME_CHECK_SAMPLES.find((sample) => sample === asked);
}

/** Chooses what the payout screens talk to: the demo when it is switched on, otherwise the API. */
export function resolvePayoutsClient(stub: StubModule | null = loadStub()): PayoutsClient {
  if (!stub) {
    return createApiPayoutsClient();
  }
  // On the web, `?sample=empty` starts as a new client and `?payouts=offline` fails every read.
  return stub.createStubPayoutsClient({
    sample: OPENED_WITH.includes("sample=empty") ? "empty" : "default",
    offline: OPENED_WITH.includes("payouts=offline"),
    // `?namecheck=not_answered` (or checking, limit_reached, waiting_for_id, no_name) adds an
    // Airtel number at that point of its name check.
    nameCheck: nameCheckSample(OPENED_WITH),
  });
}
