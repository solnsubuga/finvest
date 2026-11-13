import { createContext, ReactNode, useCallback, useContext, useMemo, useState } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { resolveSupportClient } from "./resolveSupport";
import { SupportClient } from "./types";

type Value = {
  client: SupportClient;
  /** Unread notifications, for the bell. Null until first read, or when it cannot be read. */
  unread: number | null;
  setUnread: (count: number | null) => void;
  /** Reads the unread count again. Quiet on failure: the bell just shows no dot. */
  refreshUnread: () => Promise<void>;
};

const SupportContext = createContext<Value | null>(null);

export function SupportProvider({
  children,
  client,
}: {
  children: ReactNode;
  /** Tests can pass their own client. */
  client?: SupportClient;
}) {
  const { session } = useAuth();
  const account = session?.phone ?? null;
  // One client per signed-in account. Signing out drops it, and what it held.
  const resolved = useMemo(() => client ?? resolveSupportClient(account), [client, account]);
  const [counted, setCounted] = useState<{ account: string | null; unread: number | null }>({
    account,
    unread: null,
  });
  // A count read for one account is never shown to the next.
  const unread = counted.account === account ? counted.unread : null;
  const setUnread = useCallback(
    (count: number | null) => setCounted({ account, unread: count }),
    [account],
  );

  const refreshUnread = useCallback(async () => {
    try {
      const page = await resolved.listNotifications();
      setUnread(page.unread);
    } catch {
      setUnread(null);
    }
  }, [resolved, setUnread]);

  const value = useMemo(
    () => ({ client: resolved, unread, setUnread, refreshUnread }),
    [resolved, unread, setUnread, refreshUnread],
  );
  return <SupportContext.Provider value={value}>{children}</SupportContext.Provider>;
}

export function useSupport(): Value {
  const value = useContext(SupportContext);
  if (!value) {
    throw new Error("useSupport must be used inside SupportProvider");
  }
  return value;
}
