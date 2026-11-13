import { createContext, ReactNode, useContext, useState } from "react";

import { resolveInvestingClient } from "./resolveInvesting";
import { InvestingClient } from "./types";

const InvestingContext = createContext<InvestingClient | null>(null);

export function InvestingProvider({
  children,
  client,
}: {
  children: ReactNode;
  /** Tests can pass their own client. */
  client?: InvestingClient;
}) {
  const [resolved] = useState<InvestingClient>(() => client ?? resolveInvestingClient());
  return <InvestingContext.Provider value={resolved}>{children}</InvestingContext.Provider>;
}

export function useInvesting(): InvestingClient {
  const value = useContext(InvestingContext);
  if (!value) {
    throw new Error("useInvesting must be used inside InvestingProvider");
  }
  return value;
}
