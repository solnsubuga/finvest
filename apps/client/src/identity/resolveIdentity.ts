import { createApiIdentityClient } from "./apiIdentityClient";
import { IdentityClient } from "./types";

type StubModule = typeof import("./stubIdentityClient");

/** The demo account that starts with an approved check. */
export const SEEDED_DEMO_PHONE = "+256772123456";

/**
 * The in-memory demo approves identities nobody checked, so it must never be
 * what a real client reaches. It follows the demo sign-in: loaded in tests, and
 * in a build only when EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose. Written
 * inline, so the bundler leaves the stub out of a normal production build. CI
 * checks that it did.
 */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubIdentityClient") as StubModule;
  }
  return null;
}

/** Chooses what the ID check screens talk to: the demo when it is switched on, otherwise the API. */
export function resolveIdentityClient(phone: string | null, stub: StubModule | null = loadStub()): IdentityClient {
  if (stub) {
    return stub.createStubIdentityClient({ seeded: phone === SEEDED_DEMO_PHONE });
  }
  return createApiIdentityClient();
}
