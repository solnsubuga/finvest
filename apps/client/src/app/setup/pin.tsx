import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { forgetUnlockKey } from "@/auth/biometric";
import { PIN_LENGTH, pinProblem } from "@/auth/pin";
import { signupSteps, stepOf } from "@/auth/signupSteps";
import { AuthError, describeAuthError } from "@/auth/types";
import { StepProgress } from "@/components/Blocks";
import { PinDots, PinPad } from "@/components/pin/PinPad";
import { Screen } from "@/components/Screen";
import { needsSignInAgain, SignInAgain } from "@/components/SignInAgain";
import { Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

/**
 * Create your PIN, from the design canvas: type it, then type it again. It
 * opens the app on this phone. A new log in on the phone comes here too,
 * because a PIN belongs to one session.
 */
export default function CreatePin() {
  const router = useRouter();
  const { client, session, setSession, pinApplies, justSignedUp, setJustSignedUp } = useAuth();
  const [first, setFirst] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);

  if (!session) {
    return <Redirect href="/welcome" />;
  }
  if (!pinApplies) {
    return <Redirect href="/" />;
  }

  const offerPasskey = justSignedUp && signupSteps(true).includes("passkey");

  const save = async (chosen: string) => {
    setBusy(true);
    try {
      await client.setPin(chosen);
      // A new PIN starts without fingerprint or face unlock, as the server forgets the key.
      await forgetUnlockKey();
      setSession({ ...session, pinSet: true });
      if (offerPasskey) {
        router.replace("/setup/passkey");
      } else {
        setJustSignedUp(false);
        router.replace("/");
      }
    } catch (caught) {
      if (needsSignInAgain(caught)) {
        setStale(true);
      } else {
        setError(describeAuthError(caught));
        setFirst(null);
        setPin("");
      }
      setBusy(false);
    }
  };

  const type = (next: string) => {
    setPin(next);
    setError(null);
    if (next.length < PIN_LENGTH) return;
    if (first === null) {
      if (pinProblem(next)) {
        setError(describeAuthError(new AuthError("pin_too_simple")));
        setPin("");
        return;
      }
      setFirst(next);
      setPin("");
      return;
    }
    if (next !== first) {
      setError("Those PINs did not match. Choose your PIN again.");
      setFirst(null);
      setPin("");
      return;
    }
    void save(next);
  };

  if (stale) {
    return (
      <Screen>
        <Stack.Screen options={{ title: "Create your PIN" }} />
        <SignInAgain returnTo="/setup/pin" />
      </Screen>
    );
  }

  return (
    <Screen
      dark
      signedIn
      footer={
        offerPasskey ? (
          <Txt size={fontSizes.sm} color={colors.onBlackMuted} center>
            Next you can add a passkey for your second step.
          </Txt>
        ) : undefined
      }
    >
      <Stack.Screen options={{ title: "Create your PIN" }} />
      {justSignedUp ? <StepProgress {...stepOf("pin", true)} onDark /> : null}
      <View style={styles.top}>
        <Title onDark>{first === null ? "Create a 5 digit PIN" : "Type your PIN again"}</Title>
        <Txt color={colors.onBlackMuted} center>
          {first === null
            ? "It opens the app on this phone. Your password is still needed to sign in on a new device."
            : "So we know you typed the PIN you meant."}
        </Txt>
        <View style={{ marginTop: 12 }}>
          <PinDots count={pin.length} invalid={Boolean(error)} />
        </View>
        <View style={{ minHeight: 44, justifyContent: "center" }} accessibilityLiveRegion="polite">
          <Txt
            size={fontSizes.sm}
            weight={error ? "semibold" : "regular"}
            color={error ? colors.lime : colors.onBlackMuted}
            center
          >
            {error ?? "Avoid repeated digits and runs like 12345."}
          </Txt>
        </View>
      </View>
      <PinPad value={pin} onChange={type} disabled={busy} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { marginTop: 32, alignItems: "center", gap: 10, marginBottom: 20 },
});
