import { createContext, useContext } from "react";
import type { PasskeyChallenge } from "./api.ts";
import type { StaffSession } from "./types.ts";

export interface AuthState {
  session: StaffSession | null;
  /** True until the first session check has finished. */
  loading: boolean;
  startSignIn(email: string, password: string): Promise<PasskeyChallenge>;
  finishSignIn(challenge: PasskeyChallenge): Promise<void>;
  signOut(): Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside an AuthProvider");
  }
  return value;
}
