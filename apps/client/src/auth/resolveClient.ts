import { AuthClient, AuthError } from "./types";

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
 * client reaches. It is loaded in development, and in a production build only
 * when EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose at build time (for previews
 * and screenshots).
 *
 * The condition is written inline, and the stub is required inside it, so the
 * bundler can see it is false in a normal production build and leave the stub
 * out of the bundle altogether. CI checks that it did.
 */
function loadStub(): StubModule | null {
  if (__DEV__ || process.env.EXPO_PUBLIC_DEMO_AUTH === "1") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubAuthClient") as StubModule;
  }
  return null;
}

/** Chooses what the screens talk to. Without the stub, every sign-in is refused. */
export function resolveAuthClient(stub: StubModule | null = loadStub()): ResolvedClient {
  if (stub) {
    return { client: stub.createStubAuthClient(), demo: true, demoCode: stub.DEMO_CODE };
  }
  return { client: unavailableAuthClient, demo: false, demoCode: null };
}

const refuse = async (): Promise<never> => {
  throw new AuthError("unavailable");
};

const unavailableAuthClient: AuthClient = {
  startSignup: refuse,
  confirmSignupCode: refuse,
  completeSignup: refuse,
  startLogin: refuse,
  verifyWithPasskey: refuse,
  verifyWithAuthenticator: refuse,
  sendLoginCode: refuse,
  verifyWithCode: refuse,
  listSecondSteps: refuse,
  addPasskey: refuse,
  startAuthenticatorSetup: refuse,
  confirmAuthenticatorSetup: refuse,
  signOut: async () => {},
};
