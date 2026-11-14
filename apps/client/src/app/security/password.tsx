import { useCallback, useState } from "react";
import { View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { checkPassword, passwordIsAcceptable } from "@/auth/password";
import { BackButton } from "@/components/BackButton";
import { Card, CheckRow, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { ConfirmItIsYou, useConfirmedChange } from "@/components/ConfirmItIsYou";
import { Done } from "@/components/Done";
import { PasswordField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

/** Sign in and security: change the password, after a passkey or the authenticator app. */
export default function ChangePassword() {
  const router = useRouter();
  const { client, session } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [done, setDone] = useState(false);

  const change = useCallback(() => client.changePassword(current, next), [client, current, next]);
  const onDone = useCallback(() => setDone(true), []);
  const { run, busy, error, confirming, setConfirming } = useConfirmedChange(change, onDone);

  if (!session) return null;
  if (done) {
    return (
      <Done
        title="Password changed"
        detail="Use your new password the next time you log in. We signed out your other devices and told you by SMS and email."
        action="Back to security"
        onPress={() => router.dismissTo("/security")}
      />
    );
  }

  const checks = checkPassword(next, again, session.phone);
  const ready = current.length > 0 && passwordIsAcceptable(checks);

  return (
    <Screen
      signedIn
      footer={
        confirming ? null : (
          <Button label="Change password" onPress={run} loading={busy} disabled={!ready} testID="password-change" />
        )
      }
    >
      <Stack.Screen options={{ title: "Change password" }} />
      <BackButton fallback="/security" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Sign in and security</Eyebrow>
        <Title>Change your password</Title>
        <Txt color={colors.muted}>Your other devices are signed out when it changes.</Txt>
      </View>
      <View style={{ marginTop: 20, gap: 14 }}>
        {confirming ? (
          <ConfirmItIsYou onConfirmed={run} onCancel={() => setConfirming(false)} />
        ) : null}
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <PasswordField label="Current password" value={current} onChangeText={setCurrent} testID="password-current" />
        <PasswordField isNew label="New password" value={next} onChangeText={setNext} testID="password-new" />
        <PasswordField isNew label="Type it again" value={again} onChangeText={setAgain} testID="password-again" />
        <Card padded={false}>
          <View style={{ paddingVertical: 10, paddingHorizontal: 16 }}>
            <CheckRow met={checks.longEnough}>At least 10 characters</CheckRow>
            <CheckRow met={checks.notPersonal}>Does not contain your mobile number</CheckRow>
            <CheckRow met={checks.matches}>Matches in both boxes</CheckRow>
          </View>
        </Card>
      </View>
    </Screen>
  );
}
