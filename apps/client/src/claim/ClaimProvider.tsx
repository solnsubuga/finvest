import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { useIdentity } from "@/identity/IdentityProvider";
import { IdentityClient } from "@/identity/types";

import { createApiClaimClient } from "./apiClaimClient";
import { ClaimClient } from "./types";

type StubModule = typeof import("./stubClaimClient");

/** The demo follows the demo sign-in: tests, or EXPO_PUBLIC_DEMO_AUTH=1 on purpose. */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubClaimClient") as StubModule;
  }
  return null;
}

export function resolveClaimClient(
  phone: string | null,
  identity: IdentityClient,
  stub: StubModule | null = loadStub(),
): ClaimClient {
  if (stub) {
    // The API hands the full ID check in with the claim, so the demo hands in the demo's check.
    return stub.createStubClaimClient({ phone, submitIdCheck: () => identity.submit() });
  }
  return createApiClaimClient();
}

const ClaimContext = createContext<ClaimClient | null>(null);

/** One claim client per signed-in number, so the demo never shows the last person's claim. */
export function ClaimProvider({ children, client }: { children: ReactNode; client?: ClaimClient }) {
  const { session } = useAuth();
  const { client: identity } = useIdentity();
  const phone = session?.phone ?? null;
  const resolved = useMemo(() => client ?? resolveClaimClient(phone, identity), [client, phone, identity]);
  return <ClaimContext.Provider value={resolved}>{children}</ClaimContext.Provider>;
}

export function useClaimClient(): ClaimClient {
  const value = useContext(ClaimContext);
  if (!value) {
    throw new Error("useClaimClient must be used inside ClaimProvider");
  }
  return value;
}
