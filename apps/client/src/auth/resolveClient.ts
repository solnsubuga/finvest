import { createApiAuthClient } from "./apiAuthClient";
import { AuthClient } from "./types";

type StubModule = typeof import("./stubAuthClient");

export type ResolvedClient = {
  client: AuthClient;
  /** True while the in-memory demo is answering. Every screen says so. */
  demo: boolean;
  /** The SMS code the demo accepts, shown on screen so it can be tried. */
  demoCode: string | null;
};

/**
 * The in-memory demo accepts a fixed code, so it must never be what a real
 * client reaches. It answers in tests, and in a build only when
 * EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose at build time (for previews).
 * Development builds talk to the API at EXPO_PUBLIC_API_URL, normally a local
 * backend.
 *
 * The condition is written inline, and the stub is required inside it, so the
 * bundler can see it is false in a normal production build and leave the stub
 * out of the bundle altogether. CI checks that it did.
 */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubAuthClient") as StubModule;
  }
  return null;
}

/** Chooses what the screens talk to: the demo when it is switched on, otherwise the API. */
export function resolveAuthClient(stub: StubModule | null = loadStub()): ResolvedClient {
  if (stub) {
    return { client: stub.createStubAuthClient(), demo: true, demoCode: stub.DEMO_CODE };
  }
  return { client: createApiAuthClient(), demo: false, demoCode: null };
}
