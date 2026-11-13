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
  return stub ? stub.createStubInvestingClient({ sample: demoSample() }) : createApiInvestingClient();
}

/** On the web, opening the demo at `/?sample=empty` starts as a new client with no investments. */
function demoSample(): "default" | "empty" {
  const search = typeof window !== "undefined" ? window.location?.search : undefined;
  return search?.includes("sample=empty") ? "empty" : "default";
}
