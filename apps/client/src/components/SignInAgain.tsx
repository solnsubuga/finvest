import { useState } from "react";
import { View } from "react-native";

import { ReturnTo, useAuth } from "@/auth/AuthProvider";
import { AuthError, describeAuthError } from "@/auth/types";

import { Notice } from "./Blocks";
import { Button } from "./Button";
import { Title } from "./Txt";

/** True when the server wants a fresh log in before a sign-in method changes. */
export function needsSignInAgain(error: unknown): boolean {
  return error instanceof AuthError && error.code === "sign_in_again";
}

/**
 * Shown in place of a set-up when the session is too old to change how the
 * client signs in. Logging out lands on the welcome screen, ready to log in.
 */
export function SignInAgain({ returnTo }: { returnTo: ReturnTo }) {
  const { client, setSession, setReturnTo } = useAuth();
  const [busy, setBusy] = useState(false);

  const logOut = async () => {
    setBusy(true);
    setReturnTo(returnTo);
    try {
      await client.signOut();
    } finally {
      setSession(null);
    }
  };

  return (
    <View style={{ marginTop: 28, gap: 12 }}>
      <Title>Log in again first</Title>
      <Notice tone="warning">{describeAuthError(new AuthError("sign_in_again"))}</Notice>
      <Button label="Log in again" height={52} onPress={logOut} loading={busy} testID="sign-in-again" />
    </View>
  );
}
