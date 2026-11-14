import { useEffect } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { registerPush } from "./registration";

/**
 * After each sign-in, sends this phone's push token if the client already
 * allowed push. It never shows the phone's prompt: that is asked for only
 * from Notification settings, where the client chooses it.
 */
export function usePushRegistration(): void {
  const { session, demo } = useAuth();
  const phone = session?.phone ?? null;
  useEffect(() => {
    if (!phone || demo) {
      return;
    }
    void registerPush({ ask: false });
  }, [phone, demo]);
}
