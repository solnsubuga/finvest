import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { InsightsClient, InsightsError } from "./types";

type StubModule = typeof import("./stubInsightsClient");

/** The demo follows the payments demo: development, or EXPO_PUBLIC_DEMO_AUTH=1 on purpose. */
function loadStub(): StubModule | null {
  if (__DEV__ || process.env.EXPO_PUBLIC_DEMO_AUTH === "1") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubInsightsClient") as StubModule;
  }
  return null;
}

export function resolveInsightsClient(
  phone: string | null,
  stub: StubModule | null = loadStub(),
): InsightsClient {
  if (stub) {
    return stub.createStubInsightsClient(phone);
  }
  return {
    getInsights: async () => {
      throw new InsightsError();
    },
  };
}

const InsightsContext = createContext<InsightsClient | null>(null);

export function InsightsProvider({
  children,
  client,
}: {
  children: ReactNode;
  client?: InsightsClient;
}) {
  const { session } = useAuth();
  const phone = session?.phone ?? null;
  // The demo keeps each signed-in number's data apart, so it is made for the current number.
  const resolved = useMemo(() => client ?? resolveInsightsClient(phone), [client, phone]);
  return <InsightsContext.Provider value={resolved}>{children}</InsightsContext.Provider>;
}

export function useInsights(): InsightsClient {
  const value = useContext(InsightsContext);
  if (!value) {
    throw new Error("useInsights must be used inside InsightsProvider");
  }
  return value;
}
