import { createStubAuthClient } from "./stubAuthClient";
import { AuthClient, AuthError } from "./types";

export type ResolvedClient = { client: AuthClient; demo: boolean };

type Environment = { development: boolean; demoFlag: string | undefined };

/**
 * Chooses what the screens talk to.
 *
 * The in-memory stub accepts a fixed code, so it must never be what a real
 * client reaches. It runs in development, and in a production build only when
 * EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose at build time (for previews and
 * screenshots). Otherwise every call fails closed until the real API client
 * exists.
 */
export function resolveAuthClient(
  environment: Environment = {
    development: __DEV__,
    demoFlag: process.env.EXPO_PUBLIC_DEMO_AUTH,
  },
): ResolvedClient {
  if (environment.development || environment.demoFlag === "1") {
    return { client: createStubAuthClient(), demo: true };
  }
  return { client: unavailableAuthClient, demo: false };
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
  sendLoginCode: refuse,
  verifyWithCode: refuse,
  signOut: async () => {},
};
