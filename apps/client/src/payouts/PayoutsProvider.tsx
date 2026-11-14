import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { resolvePayoutsClient } from "./resolvePayouts";
import { PayoutsClient } from "./types";

const PayoutsContext = createContext<PayoutsClient | null>(null);

export function PayoutsProvider({
  children,
  client,
}: {
  children: ReactNode;
  /** Tests can pass their own client. */
  client?: PayoutsClient;
}) {
  const { session } = useAuth();
  const phone = session?.phone ?? null;
  // A new sign-in starts from fresh demo data, never another number's.
  const resolved = useMemo(() => client ?? resolvePayoutsClient(), [client, phone]); // eslint-disable-line react-hooks/exhaustive-deps
  return <PayoutsContext.Provider value={resolved}>{children}</PayoutsContext.Provider>;
}

export function usePayouts(): PayoutsClient {
  const value = useContext(PayoutsContext);
  if (!value) {
    throw new Error("usePayouts must be used inside PayoutsProvider");
  }
  return value;
}
