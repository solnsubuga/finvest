import { createContext, ReactNode, useContext, useState } from "react";

import { resolveBondsClient } from "./resolveBonds";
import { BondsClient } from "./types";

const BondsContext = createContext<BondsClient | null>(null);

export function BondsProvider({
  children,
  client,
}: {
  children: ReactNode;
  /** Tests can pass their own client. */
  client?: BondsClient;
}) {
  const [resolved] = useState<BondsClient>(() => client ?? resolveBondsClient());
  return <BondsContext.Provider value={resolved}>{children}</BondsContext.Provider>;
}

export function useBonds(): BondsClient {
  const value = useContext(BondsContext);
  if (!value) {
    throw new Error("useBonds must be used inside BondsProvider");
  }
  return value;
}
