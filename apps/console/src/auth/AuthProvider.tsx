import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type { AuthApi, PasskeyChallenge } from "./api.ts";
import { AuthContext, type AuthState } from "./context.ts";
import type { StaffSession } from "./types.ts";

interface AuthProviderProps {
  api: AuthApi;
  children: ReactNode;
}

export function AuthProvider({ api, children }: AuthProviderProps) {
  const [session, setSession] = useState<StaffSession | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .getSession()
      .catch(() => null)
      .then((current) => {
        if (cancelled) return;
        setSession(current);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const startSignIn = useCallback(
    (email: string, password: string) => api.startSignIn(email, password),
    [api],
  );

  const finishSignIn = useCallback(
    async (challenge: PasskeyChallenge) => {
      setSession(await api.finishSignIn(challenge));
    },
    [api],
  );

  const signOut = useCallback(async () => {
    // Leave the console even if the request fails: staying signed in on screen
    // after pressing Sign Out is the worse outcome.
    try {
      await api.signOut();
    } finally {
      setSession(null);
    }
  }, [api]);

  const value = useMemo<AuthState>(
    () => ({ session, loading, startSignIn, finishSignIn, signOut }),
    [session, loading, startSignIn, finishSignIn, signOut],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
