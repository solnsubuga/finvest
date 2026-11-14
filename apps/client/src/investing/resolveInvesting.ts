import { createApiInvestingClient } from "./apiInvestingClient";
import { InvestingClient } from "./types";

type StubModule = typeof import("./stubInvestingClient");

/**
 * The in-memory demo invents investments and interest, so it must never be
 * what a real client reaches. It follows the demo sign-in: loaded in tests, and
 * in a build only when EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose. Written
 * inline, so the bundler leaves the stub out of a normal production build.
 */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubInvestingClient") as StubModule;
  }
  return null;
}

/** Chooses what the investing screens talk to: the demo when it is switched on, otherwise the API. */
export function resolveInvestingClient(stub: StubModule | null = loadStub()): InvestingClient {
  return stub ? stub.createStubInvestingClient({ sample: demoSample(), ...demoWithdrawal(), failPortfolio: demoSearch().includes("portfolio=fail") }) : createApiInvestingClient();
}

/**
 * On the web, `?portfolio=fail` fails the Home and Portfolio totals, `?accounts=none` has no approved accounts, `?withdraw=lost` loses the first
 * withdrawal's answer, `?matured=waiting` adds matured money waiting for an account,
 * `?transfers=none` starts with nothing sent or received, and `?review=low` holds any send
 * over UGX 1,000,000 for staff, to show that state.
 */
function demoWithdrawal(): {
  accounts: "default" | "none";
  loseFirstWithdrawal: boolean;
  maturedWaiting: boolean;
  transfers: "default" | "none";
  reviewAbove?: number;
} {
  const search = typeof window !== "undefined" ? (window.location?.search ?? "") : "";
  return {
    accounts: search.includes("accounts=none") ? "none" : "default",
    loseFirstWithdrawal: search.includes("withdraw=lost"),
    maturedWaiting: search.includes("matured=waiting"),
    transfers: search.includes("transfers=none") ? "none" : "default",
    ...(search.includes("review=low") ? { reviewAbove: 1_000_000 } : {}),
  };
}

/** On the web, opening the demo at `/?sample=empty` starts as a new client with no investments. */
function demoSample(): "default" | "empty" {
  const search = typeof window !== "undefined" ? window.location?.search : undefined;
  return search?.includes("sample=empty") ? "empty" : "default";
}

function demoSearch(): string {
  return typeof window !== "undefined" ? (window.location?.search ?? "") : "";
}
