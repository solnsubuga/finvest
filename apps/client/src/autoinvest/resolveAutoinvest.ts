import { AutoinvestClient, AutoinvestError } from "./types";

type StubModule = typeof import("./stubAutoinvestClient");

/**
 * The in-memory demo is never what a real client reaches. Like the payments
 * demo, it is loaded in development, and in a production build only when
 * EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose at build time. The condition is
 * inline so the bundler leaves the stub out of a normal production build.
 */
function loadStub(): StubModule | null {
  if (__DEV__ || process.env.EXPO_PUBLIC_DEMO_AUTH === "1") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubAutoinvestClient") as StubModule;
  }
  return null;
}

/** Without the stub, every call is refused until the real client exists. */
export function resolveAutoinvestClient(
  phone: string | null,
  stub: StubModule | null = loadStub(),
): AutoinvestClient {
  if (stub) {
    return stub.createStubAutoinvestClient(phone);
  }
  const refuse = async (): Promise<never> => {
    throw new AutoinvestError("unavailable");
  };
  return {
    listProducts: refuse,
    listPlans: refuse,
    getPlan: refuse,
    createPlan: refuse,
    pausePlan: refuse,
    resumePlan: refuse,
    stopPlan: refuse,
  };
}
