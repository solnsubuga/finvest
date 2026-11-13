import { InvestingClient, InvestingError } from "./types";

type StubModule = typeof import("./stubInvestingClient");

/**
 * The in-memory demo invents investments and interest, so it must never be
 * what a real client reaches. Like the demo payments, it loads in development
 * and in a production build only when EXPO_PUBLIC_DEMO_AUTH=1 was set at build
 * time. The condition is inline so the bundler drops the stub otherwise.
 */
function loadStub(): StubModule | null {
  if (__DEV__ || process.env.EXPO_PUBLIC_DEMO_AUTH === "1") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubInvestingClient") as StubModule;
  }
  return null;
}

/** Chooses what the investing screens talk to. Without the stub, every request is refused. */
export function resolveInvestingClient(stub: StubModule | null = loadStub()): InvestingClient {
  return stub ? stub.createStubInvestingClient({ sample: demoSample() }) : unavailableInvestingClient;
}

/** On the web, opening the demo at `/?sample=empty` starts as a new client with no investments. */
function demoSample(): "default" | "empty" {
  const search = typeof window !== "undefined" ? window.location?.search : undefined;
  return search?.includes("sample=empty") ? "empty" : "default";
}

const refuse = async (): Promise<never> => {
  throw new InvestingError("unavailable");
};

export const unavailableInvestingClient: InvestingClient = {
  listProducts: refuse,
  quote: refuse,
  requestInvestment: refuse,
  listInvestments: refuse,
  getInvestment: refuse,
};
