import { useState } from "react";
import { View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { checkPassword, passwordIsAcceptable } from "@/auth/password";
import { formatE164 } from "@/auth/phone";
import { AuthError, describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Card, CheckRow, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import { PasswordField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

/**
 * Forgot password, step 2: the texted code and the new password together,
 * because the server checks the code only when the password is set.
 */
export default function ResetNew() {
  const router = useRouter();
  const { client, reset, setReset } = useAuth();
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [codeWrong, setCodeWrong] = useState(false);
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);

  if (!reset) {
    return <Redirect href="/reset" />;
  }

  const checks = checkPassword(password, confirmation, reset.phone);
  const ready = code.length === CODE_LENGTH && passwordIsAcceptable(checks);

  const resend = async () => {
    setResending(true);
    setError(null);
    setResent(false);
    try {
      // A new code replaces the old one, so the boxes start empty.
      setReset(await client.startPasswordReset(reset.phone));
      setCode("");
      setCodeWrong(false);
      setResent(true);
    } catch (caught) {
      setError(describeAuthError(caught));
    } finally {
      setResending(false);
    }
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    setCodeWrong(false);
    setResent(false);
    try {
      await client.completePasswordReset(reset.challengeId, code, password);
      setReset(null);
      router.replace("/reset/done");
    } catch (caught) {
      setError(describeAuthError(caught));
      if (caught instanceof AuthError && caught.code === "invalid_code") {
        setCodeWrong(true);
        setCode("");
      }
      setBusy(false);
    }
  };

  return (
    <Screen
      footer={
        <Button
          label="Set new password"
          onPress={submit}
          loading={busy}
          disabled={!ready}
          testID="reset-complete"
        />
      }
    >
      <Stack.Screen options={{ title: "Choose a new password" }} />
      <BackButton fallback="/reset" />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>Choose a new password</Title>
        <Txt color={colors.muted}>
          If <Txt weight="bold">{formatE164(reset.phone)}</Txt> has a Finvest account, we have
          texted it a 6 digit code.
        </Txt>
      </View>

      <View style={{ marginTop: 20, gap: 14 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        {resent ? <Notice tone="neutral">We sent a new code. Only the newest one works.</Notice> : null}
        <View style={{ gap: 8 }}>
          <Eyebrow>Code from the SMS</Eyebrow>
          <CodeInput
            value={code}
            onChangeText={(next) => {
              setCode(next);
              setCodeWrong(false);
            }}
            invalid={codeWrong}
          />
          <View style={{ flexDirection: "row", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
            <TextLink
              label={resending ? "Sending…" : "Send the code again"}
              onPress={() => (resending ? undefined : resend())}
              testID="reset-resend"
            />
            <TextLink
              label="Wrong number? Start again"
              onPress={() => router.replace("/reset")}
            />
          </View>
        </View>
        <PasswordField
          isNew
          label="New password"
          value={password}
          onChangeText={(next) => {
            setPassword(next);
            setError(null);
          }}
          testID="password-input"
        />
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
          We never ask for your password or this code by phone, SMS or WhatsApp. If someone does,
          do not share it.
        </Txt>
      </View>
    </Screen>
  );
}
