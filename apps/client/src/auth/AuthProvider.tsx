import { createContext, ReactNode, useContext, useMemo, useState } from "react";

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
};

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
  const [signup, setSignup] = useState<SignupChallenge | null>(null);
  const [ticket, setTicket] = useState<SignupTicket | null>(null);
  const [login, setLogin] = useState<LoginChallenge | null>(null);

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
    }),
    [resolved, session, signup, ticket, login],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}
