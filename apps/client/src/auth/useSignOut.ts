import { useState } from "react";

import { useAuth } from "./AuthProvider";

/**
 * Logs this device out. Leaving always works, even when the server cannot be
 * told, so the session is cleared whatever the call returns.
 */
export function useSignOut(): { signOut: () => Promise<void>; busy: boolean } {
  const { client, setSession } = useAuth();
  const [busy, setBusy] = useState(false);
  const signOut = async () => {
    setBusy(true);
    try {
      await client.signOut();
    } finally {
      setSession(null);
    }
  };
  return { signOut, busy };
}
