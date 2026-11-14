import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { createApiOrganisationsClient } from "./apiOrganisationsClient";
import { OrganisationsClient } from "./types";

type StubModule = typeof import("./stubOrganisationsClient");

/** The demo follows the demo sign-in: tests, or EXPO_PUBLIC_DEMO_AUTH=1 on purpose. */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubOrganisationsClient") as StubModule;
  }
  return null;
}

export function resolveOrganisationsClient(
  phone: string | null,
  stub: StubModule | null = loadStub(),
): OrganisationsClient {
  if (stub) {
    return stub.createStubOrganisationsClient(phone);
  }
  return createApiOrganisationsClient();
}

const OrganisationsContext = createContext<OrganisationsClient | null>(null);

export function OrganisationsProvider({ children, client }: { children: ReactNode; client?: OrganisationsClient }) {
  const { session } = useAuth();
  const phone = session?.phone ?? null;
  // One client per signed-in number, so the demo never shows the last person's applications.
  const resolved = useMemo(() => client ?? resolveOrganisationsClient(phone), [client, phone]);
  return <OrganisationsContext.Provider value={resolved}>{children}</OrganisationsContext.Provider>;
}

export function useOrganisations(): OrganisationsClient {
  const value = useContext(OrganisationsContext);
  if (!value) {
    throw new Error("useOrganisations must be used inside OrganisationsProvider");
  }
  return value;
}
