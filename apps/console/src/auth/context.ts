import { createContext, useContext } from "react";
import type { AuthenticatorSetup, Enrolment, SecondStepAnswer, SignInChallenge } from "./api.ts";
import type { StaffSession } from "./types.ts";

export interface AuthState {
  session: StaffSession | null;
  /** True until the first session check has finished. */
  loading: boolean;
  startSignIn(email: string, password: string): Promise<SignInChallenge>;
  finishSignIn(challenge: SignInChallenge, answer: SecondStepAnswer): Promise<void>;
  signOut(): Promise<void>;
  getEnrolment(token: string): Promise<Enrolment>;
  enrolWithPasskey(token: string, password: string): Promise<void>;
  startAuthenticatorEnrolment(token: string): Promise<AuthenticatorSetup>;
  enrolWithAuthenticator(token: string, password: string, code: string): Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside an AuthProvider");
  }
  return value;
}
