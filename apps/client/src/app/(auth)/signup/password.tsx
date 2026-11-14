import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Redirect, Stack } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { signupSteps, stepOf } from "@/auth/signupSteps";
import { checkPassword, passwordIsAcceptable, passwordStrength } from "@/auth/password";
import { describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Card, CheckRow, Notice, StepProgress } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { PasswordField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

export default function SignupPassword() {
  const { client, ticket, setTicket, setSignup, setSession, setReturnTo, setJustSignedUp, pinApplies } =
    useAuth();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!ticket) {
    return <Redirect href="/signup" />;
  }

  const checks = checkPassword(password, confirmation, ticket.phone);
  const strength = passwordStrength(password);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const session = await client.completeSignup(ticket.ticket, password);
      setTicket(null);
      setSignup(null);
      setJustSignedUp(true);
      // The auth layout sends a signed-in client on: a phone to choose its PIN (the app's
      // layout asks for one), anything that can make a passkey to the offer of one.
      if (!pinApplies && signupSteps(false).includes("passkey")) setReturnTo("/setup/passkey");
      setSession(session);
    } catch (caught) {
      setError(describeAuthError(caught));
      setBusy(false);
    }
  };

  return (
    <Screen
      footer={
        <Button
          label="Continue"
          onPress={submit}
          loading={busy}
          disabled={!passwordIsAcceptable(checks)}
          testID="password-submit"
        />
      }
    >
      <Stack.Screen options={{ title: "Create a password" }} />
      <View style={styles.top}>
        <BackButton fallback="/signup" />
        <StepProgress {...stepOf("password", pinApplies)} />
      </View>
      <View style={{ marginTop: 36, gap: 10 }}>
        <Title>Create a password</Title>
        <Txt color={colors.muted}>
          You will need it whenever you sign in on a new phone or on the web.
        </Txt>
      </View>

      <View style={{ marginTop: 24, gap: 14 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <View style={{ gap: 6 }}>
          <PasswordField
            isNew
            label="Password"
            value={password}
            onChangeText={(next) => {
              setPassword(next);
              setError(null);
            }}
            autoFocus
            testID="password-input"
          />
          <View style={styles.meter}>
            <View
              style={{
                width: `${Math.round(strength.fraction * 100)}%`,
                height: 6,
                backgroundColor: strength.ok ? colors.success : colors.muted,
              }}
            />
          </View>
          <Txt
            size={fontSizes.sm}
            weight="bold"
            color={strength.ok ? colors.ink : colors.muted}
            accessibilityLiveRegion="polite"
          >
            {strength.label || " "}
          </Txt>
        </View>
        <PasswordField
          isNew
          label="Type it again"
          value={confirmation}
          onChangeText={setConfirmation}
          testID="password-confirm-input"
        />
        <Card padded={false}>
          <View style={{ paddingVertical: 10, paddingHorizontal: 16 }}>
            <CheckRow met={checks.longEnough}>At least 10 characters</CheckRow>
            <CheckRow met={checks.notPersonal}>Does not contain your mobile number</CheckRow>
            <CheckRow met={checks.matches}>Matches in both boxes</CheckRow>
          </View>
        </Card>
        <Txt size={fontSizes.sm} color={colors.muted}>
          A long phrase you can remember works better than symbols. When you continue we also check
          it against known leaked passwords. We never ask for your password by phone, SMS or
          WhatsApp.
        </Txt>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: "row", alignItems: "center", gap: 16 },
  meter: { height: 6, borderRadius: 3, backgroundColor: colors.border, overflow: "hidden" },
});
