import { createContext, ReactNode, useContext, useState } from "react";

import { resolvePaymentsClient, ResolvedPayments } from "./resolvePayments";
import { PaymentsClient } from "./types";

const PaymentsContext = createContext<ResolvedPayments | null>(null);

export function PaymentsProvider({
  children,
  client,
}: {
  children: ReactNode;
  /** Tests can pass their own client. */
  client?: PaymentsClient;
}) {
  const [resolved] = useState<ResolvedPayments>(() =>
    client ? { client, demoPayableId: null } : resolvePaymentsClient(),
  );
  return <PaymentsContext.Provider value={resolved}>{children}</PaymentsContext.Provider>;
}

export function usePayments(): ResolvedPayments {
  const value = useContext(PaymentsContext);
  if (!value) {
    throw new Error("usePayments must be used inside PaymentsProvider");
  }
  return value;
}
