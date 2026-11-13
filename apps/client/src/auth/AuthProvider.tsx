import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { View } from "react-native";

import { onUnauthorized } from "@/api/http";
import { colors } from "@/theme";

import { resolveAuthClient } from "./resolveClient";
import {
  AuthClient,
  LoginChallenge,
  Session,
  SignupChallenge,
  SignupTicket,
} from "./types";

/**
 * Holds the session and the half-finished sign-up or log-in between screens.
 *
 * The in-progress steps live here, in memory, and not in route params: route
 * params end up in the address bar on the web, and a ticket or login id does
 * not belong there.
 */
type AuthState = {
  client: AuthClient;
  /** True while the in-memory stub is answering. Screens say so. */
  demo: boolean;
  demoCode: string | null;
  session: Session | null;
  setSession: (session: Session | null) => void;
  signup: SignupChallenge | null;
  setSignup: (signup: SignupChallenge | null) => void;
  ticket: SignupTicket | null;
  setTicket: (ticket: SignupTicket | null) => void;
  login: LoginChallenge | null;
  setLogin: (login: LoginChallenge | null) => void;
  /**
   * Where to go straight after the next log in. Set when a set-up screen
   * sends the client to log in again, so they come back to the same set-up.
   */
  returnTo: ReturnTo | null;
  setReturnTo: (returnTo: ReturnTo | null) => void;
};

/** Only the set-up screens, so a log in can never be sent somewhere unexpected. */
export type ReturnTo = "/security/passkey" | "/security/authenticator";

/** The return address is for logging in again right away, not for a later visit. */
export const RETURN_TO_LIFETIME_MS = 5 * 60 * 1000;


const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({
  children,
  client,
}: {
  children: ReactNode;
  /** Tests can pass their own client. */
  client?: AuthClient;
}) {
  const [resolved] = useState(() => (client ? { client, demo: false, demoCode: null } : resolveAuthClient()));
  const [session, setSession] = useState<Session | null>(null);
  // Until the device's existing session has been looked up, no screen decides
  // whether the client is signed in.
  const [restoring, setRestoring] = useState(true);
  const [signup, setSignup] = useState<SignupChallenge | null>(null);
  const [ticket, setTicket] = useState<SignupTicket | null>(null);
  const [login, setLogin] = useState<LoginChallenge | null>(null);
  const [returnTo, setReturnTo] = useState<ReturnTo | null>(null);

  // The return address is dropped after a few minutes, so a much later log in
  // goes Home and not to a set-up screen. It survives the app going to the
  // background, because reading an SMS or an authenticator code does that.
  useEffect(() => {
    if (!returnTo) {
      return undefined;
    }
    const timer = setTimeout(() => setReturnTo(null), RETURN_TO_LIFETIME_MS);
    return () => clearTimeout(timer);
  }, [returnTo]);

  useEffect(() => {
    let live = true;
    resolved.client
      .restoreSession()
      .then((restored) => {
        if (live && restored) setSession((current) => current ?? restored);
      })
      .catch(() => {
        // Offline or the API is down: start signed out. Logging in says why it cannot go on.
      })
      .finally(() => {
        if (live) setRestoring(false);
      });
    return () => {
      live = false;
    };
  }, [resolved]);

  // The API says the session has ended (it timed out, or was ended elsewhere).
  useEffect(() => onUnauthorized(() => setSession(null)), []);

  const value = useMemo(
    () => ({
      client: resolved.client,
      demo: resolved.demo,
      demoCode: resolved.demoCode,
      session,
      setSession,
      signup,
      setSignup,
      ticket,
      setTicket,
      login,
      setLogin,
      returnTo,
      setReturnTo,
    }),
    [resolved, session, signup, ticket, login, returnTo],
  );

  if (restoring) {
    return <View testID="auth-restoring" style={{ flex: 1, backgroundColor: colors.paper }} />;
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}
