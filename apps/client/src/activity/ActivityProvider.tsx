import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { createApiActivityClient } from "./apiActivityClient";
import { ActivityClient } from "./types";

type StubModule = typeof import("./stubActivityClient");

/** The demo follows the demo sign-in: tests, or EXPO_PUBLIC_DEMO_AUTH=1 on purpose. */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubActivityClient") as StubModule;
  }
  return null;
}

export function resolveActivityClient(phone: string | null, stub: StubModule | null = loadStub()): ActivityClient {
  return stub ? stub.createStubActivityClient(phone) : createApiActivityClient();
}

const ActivityContext = createContext<ActivityClient | null>(null);

export function ActivityProvider({ children, client }: { children: ReactNode; client?: ActivityClient }) {
  const { session } = useAuth();
  const phone = session?.phone ?? null;
  const resolved = useMemo(() => client ?? resolveActivityClient(phone), [client, phone]);
  return <ActivityContext.Provider value={resolved}>{children}</ActivityContext.Provider>;
}

export function useActivity(): ActivityClient {
  const value = useContext(ActivityContext);
  if (!value) {
    throw new Error("useActivity must be used inside ActivityProvider");
  }
  return value;
}
