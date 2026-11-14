import { createContext, ReactNode, useCallback, useContext, useMemo, useState } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { resolveIdentityClient } from "./resolveIdentity";
import { IdentityClient, PhotoKind } from "./types";

type Identity = {
  client: IdentityClient;
  /**
   * The photos staff asked for again that were sent from this device since,
   * by check. The API does not say which ones were retaken, so a screen
   * remembers it here, and handing in tells for sure.
   */
  retaken: (checkId: string) => PhotoKind[];
  markRetaken: (checkId: string, kind: PhotoKind) => void;
};

const IdentityContext = createContext<Identity | null>(null);

/** One identity client per signed-in client, so one person's demo check never shows to another. */
export function IdentityProvider({ children, client }: { children: ReactNode; client?: IdentityClient }) {
  const { session } = useAuth();
  const phone = session?.phone ?? null;
  const resolved = useMemo(() => client ?? resolveIdentityClient(phone), [client, phone]);
  const [retakes, setRetakes] = useState<Record<string, PhotoKind[]>>({});

  const retaken = useCallback((checkId: string) => retakes[checkId] ?? [], [retakes]);
  const markRetaken = useCallback((checkId: string, kind: PhotoKind) => {
    setRetakes((current) => {
      const known = current[checkId] ?? [];
      return known.includes(kind) ? current : { ...current, [checkId]: [...known, kind] };
    });
  }, []);

  const value = useMemo(() => ({ client: resolved, retaken, markRetaken }), [resolved, retaken, markRetaken]);
  return <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>;
}

export function useIdentity(): Identity {
  const value = useContext(IdentityContext);
  if (!value) {
    throw new Error("useIdentity must be used inside IdentityProvider");
  }
  return value;
}
