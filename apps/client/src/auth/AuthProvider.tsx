import { createContext, ReactNode, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AppState, View } from "react-native";

import { onLocked, onUnauthorized } from "@/api/http";
import { SignedOutForPins, UnlockScreen } from "@/components/pin/UnlockScreen";
import { colors } from "@/theme";

import { appPinApplies, LOCK_AFTER_MS } from "./pin";
import { resolveAuthClient } from "./resolveClient";
import {
  AuthClient,
  LoginChallenge,
  PasswordReset,
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
  reset: PasswordReset | null;
  setReset: (reset: PasswordReset | null) => void;
  /**
   * Where to go straight after the next log in. Set when a set-up screen
   * sends the client to log in again, so they come back to the same set-up.
   */
  returnTo: ReturnTo | null;
  setReturnTo: (returnTo: ReturnTo | null) => void;
  /** True on the phone app (and in the demo), where a session gets a PIN. */
  pinApplies: boolean;
  /** The app is locked: only the PIN screen shows until it is entered. */
  locked: boolean;
  setLocked: (locked: boolean) => void;
  /**
   * Too many wrong PINs ended the session. The screen that says so stays up after the session
   * is gone, until the client chooses to log in again.
   */
  signedOutForPins: boolean;
  setSignedOutForPins: (signedOut: boolean) => void;
  /** Set by finishing sign up, so the set-up screens that follow can offer a passkey. */
  justSignedUp: boolean;
  setJustSignedUp: (justSignedUp: boolean) => void;
};

/** Only the screens that change how a client signs in, so a log in can never be sent somewhere unexpected. */
export type ReturnTo =
  | "/security/passkey"
  | "/security/authenticator"
  | "/security/passkeys"
  | "/security/authenticator-off"
  | "/account/close"
  | "/account/language"
  | "/setup/pin"
  | "/setup/passkey";

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
  const [reset, setReset] = useState<PasswordReset | null>(null);
  const [returnTo, setReturnTo] = useState<ReturnTo | null>(null);
  const [locked, setLocked] = useState(false);
  const [signedOutForPins, setSignedOutForPins] = useState(false);
  // Read by the unauthorized listener, which is set up once.
  const lockedNow = useRef(false);
  useEffect(() => {
    lockedNow.current = Boolean(session) && locked;
  }, [session, locked]);
  const [justSignedUp, setJustSignedUp] = useState(false);
  const pinApplies = appPinApplies(resolved.demo);
  const backgroundSince = useRef<number | null>(null);

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
        if (!live || !restored) return;
        setSession((current) => current ?? restored);
        // Opening the app on a phone with a PIN always asks for it first.
        if (restored.locked || (pinApplies && restored.pinSet)) setLocked(true);
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
  }, [resolved, pinApplies]);

  // Back from the background after a while: the PIN again, as the API would ask.
  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        const since = backgroundSince.current;
        backgroundSince.current = null;
        if (since !== null && Date.now() - since >= LOCK_AFTER_MS) {
          setSession((current) => {
            if (current?.pinSet && pinApplies) setLocked(true);
            return current;
          });
        }
      } else if (backgroundSince.current === null) {
        backgroundSince.current = Date.now();
      }
    });
    return () => subscription.remove();
  }, [pinApplies]);

  // The API says this session wants its PIN first.
  useEffect(() => onLocked(() => setLocked(true)), []);

  // The API says the session has ended (it timed out, or was ended elsewhere).
  useEffect(
    () =>
      onUnauthorized(() => {
        // A 401 while the PIN screen is up is the fifth wrong PIN: say so, not just Log in.
        if (lockedNow.current) setSignedOutForPins(true);
        setSession(null);
        setLocked(false);
      }),
    [],
  );

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
      reset,
      setReset,
      returnTo,
      setReturnTo,
      pinApplies,
      locked,
      setLocked,
      signedOutForPins,
      setSignedOutForPins,
      justSignedUp,
      setJustSignedUp,
    }),
    [resolved, session, signup, ticket, login, reset, returnTo, pinApplies, locked, signedOutForPins, justSignedUp],
  );

  if (restoring) {
    return <View testID="auth-restoring" style={{ flex: 1, backgroundColor: colors.paper }} />;
  }

  return (
    <AuthContext.Provider value={value}>
      {signedOutForPins ? <SignedOutForPins /> : session && locked ? <UnlockScreen /> : children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}
