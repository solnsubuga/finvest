import { createApiAutoinvestClient } from "./apiAutoinvestClient";
import { AutoinvestClient } from "./types";

type StubModule = typeof import("./stubAutoinvestClient");

/**
 * The in-memory demo is never what a real client reaches. Like the payments
 * demo, it is loaded in tests, and in a build only when EXPO_PUBLIC_DEMO_AUTH=1
 * was set on purpose at build time. The condition is
 * inline so the bundler leaves the stub out of a normal production build.
 */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubAutoinvestClient") as StubModule;
  }
  return null;
}

/** Chooses what the plan screens talk to: the demo when it is switched on, otherwise the API. */
export function resolveAutoinvestClient(
  phone: string | null,
  stub: StubModule | null = loadStub(),
): AutoinvestClient {
  if (stub) {
    return stub.createStubAutoinvestClient(phone);
  }
  return createApiAutoinvestClient();
}
