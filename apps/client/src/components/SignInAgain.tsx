import { useState } from "react";
import { View } from "react-native";

import { ReturnTo, useAuth } from "@/auth/AuthProvider";
import { AuthError } from "@/auth/types";

import { Notice } from "./Blocks";
import { Button } from "./Button";
import { Title } from "./Txt";

/** True when the server wants a fresh log in before a sign-in method changes. */
export function needsSignInAgain(error: unknown): boolean {
  return error instanceof AuthError && error.code === "sign_in_again";
}

/**
 * Shown in place of a step that needs a fresh log in, such as changing how the client signs
 * in or downloading their data. Logging out lands on the welcome screen, ready to log in, and
 * logging in brings the client back to `returnTo`.
 */
export function SignInAgain({ returnTo, before = "change how you sign in" }: { returnTo: ReturnTo; before?: string }) {
  const { client, setSession, setReturnTo } = useAuth();
  const [busy, setBusy] = useState(false);

  const logOut = async () => {
    setBusy(true);
    try {
      await client.signOut();
    } finally {
      // Set together with the end of the session, in one update. Set before, while the client
      // was still signed in, the screen's own layout took it as already spent and dropped it,
      // so the log in went Home.
      setReturnTo(returnTo);
      setSession(null);
    }
  };

  return (
    <View style={{ marginTop: 28, gap: 12 }}>
      <Title>Log in again first</Title>
      <Notice tone="warning">{`For your safety, log in again before you ${before}. We will bring you straight back here.`}</Notice>
      <Button label="Log in again" height={52} onPress={logOut} loading={busy} testID="sign-in-again" />
    </View>
  );
}
