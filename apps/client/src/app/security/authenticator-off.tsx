import { useState } from "react";
import { View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { describeAuthError } from "@/auth/types";
import { useSecondSteps } from "@/auth/useSecondSteps";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Done } from "@/components/Done";
import { Screen } from "@/components/Screen";
import { needsSignInAgain, SignInAgain } from "@/components/SignInAgain";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

/** Sign in and security: turn off the authenticator app as a way to log in. */
export default function AuthenticatorOff() {
  const router = useRouter();
  const { client } = useAuth();
  const steps = useSecondSteps();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [off, setOff] = useState(false);

  const leave = () => router.dismissTo("/security");

  const turnOff = async () => {
    setBusy(true);
    setError(null);
    try {
      await client.turnOffAuthenticator();
      setOff(true);
    } catch (caught) {
      if (needsSignInAgain(caught)) {
        setStale(true);
      } else {
        setError(describeAuthError(caught));
      }
      setBusy(false);
    }
  };

  if (off) {
    return (
      <>
        <Stack.Screen options={{ title: "Authenticator app turned off" }} />
        <Done
          title="Authenticator app turned off"
          detail="Its codes no longer log you in. You can delete the Finvest entry from the app. To use one again, set it up afresh from Sign in and security."
          action="Done"
          onPress={leave}
        />
      </>
    );
  }

  if (stale) {
    return (
      <Screen>
        <Stack.Screen options={{ title: "Turn off the authenticator app" }} />
        <BackButton fallback="/security" />
        <SignInAgain returnTo="/security/authenticator-off" />
      </Screen>
    );
  }

  const left = steps.status === "ready" ? steps.methods.filter((m) => m !== "authenticator") : [];
  const onlySmsLeft = steps.status === "ready" && left.length === 1 && left[0] === "code";

  return (
    <Screen
      footer={
        <>
          <Button label="Turn off" onPress={turnOff} loading={busy} testID="confirm-authenticator-off" />
          <TextLink label="Keep it on" onPress={leave} />
        </>
      }
    >
      <Stack.Screen options={{ title: "Turn off the authenticator app" }} />
      <BackButton fallback="/security" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Sign in and security</Eyebrow>
        <Title>Turn off the authenticator app?</Title>
        <Txt color={colors.muted}>
          Codes from your authenticator app will stop working for Finvest. Do this if you lost the
          phone that has the app, or are moving to a new one.
        </Txt>
      </View>
      <View style={{ marginTop: 22, gap: 12 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        {onlySmsLeft ? (
          <Notice tone="warning">
            After this you can only log in with an SMS code, and every log in limits withdrawals to
            accounts you have used before for 24 hours. Add a passkey or set up the app again to
            avoid this.
          </Notice>
        ) : null}
      </View>
    </Screen>
  );
}
