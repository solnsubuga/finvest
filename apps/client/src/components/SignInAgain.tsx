import { useState } from "react";
import { View } from "react-native";

import { useAuth } from "@/auth/AuthProvider";
import { AuthError, describeAuthError } from "@/auth/types";

import { Notice } from "./Blocks";
import { Button } from "./Button";

/** True when the server wants a fresh log in before a sign-in method changes. */
export function needsSignInAgain(error: unknown): boolean {
  return error instanceof AuthError && error.code === "sign_in_again";
}

/**
 * Shown in place of a set-up when the session is too old to change how the
 * client signs in. Logging out lands on the welcome screen, ready to log in.
 */
export function SignInAgain() {
  const { client, setSession } = useAuth();
  const [busy, setBusy] = useState(false);

  const logOut = async () => {
    setBusy(true);
    try {
      await client.signOut();
    } finally {
      setSession(null);
    }
  };

  return (
    <View style={{ marginTop: 28, gap: 12 }}>
      <Notice tone="warning">{describeAuthError(new AuthError("sign_in_again"))}</Notice>
      <Button label="Log in again" height={52} onPress={logOut} loading={busy} testID="sign-in-again" />
    </View>
  );
}
