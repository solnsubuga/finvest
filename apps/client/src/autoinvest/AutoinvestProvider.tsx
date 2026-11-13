import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { resolveAutoinvestClient } from "./resolveAutoinvest";
import { AutoinvestClient } from "./types";

const AutoinvestContext = createContext<AutoinvestClient | null>(null);

export function AutoinvestProvider({
  children,
  client,
}: {
  children: ReactNode;
  /** Tests can pass their own client. */
  client?: AutoinvestClient;
}) {
  const { session } = useAuth();
  const phone = session?.phone ?? null;
  // The demo keeps each signed-in number's data apart, so it is made for the current number.
  const resolved = useMemo(() => client ?? resolveAutoinvestClient(phone), [client, phone]);
  return <AutoinvestContext.Provider value={resolved}>{children}</AutoinvestContext.Provider>;
}

export function useAutoinvest(): AutoinvestClient {
  const value = useContext(AutoinvestContext);
  if (!value) {
    throw new Error("useAutoinvest must be used inside AutoinvestProvider");
  }
  return value;
}
