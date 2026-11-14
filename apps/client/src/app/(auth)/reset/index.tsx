import { useState } from "react";
import { View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { toE164 } from "@/auth/phone";
import { describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { PhoneField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

/** Forgot password, step 1: the number to text a code to. */
export default function ResetStart() {
  const router = useRouter();
  const { client, setReset } = useAuth();
  const [phone, setPhone] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const e164 = toE164(phone);
  const phoneError = submitted && !e164 ? "Enter a mobile number like 772 123 456." : null;

  const submit = async () => {
    setSubmitted(true);
    setError(null);
    if (!e164) {
      return;
    }
    setBusy(true);
    try {
      setReset(await client.startPasswordReset(e164));
      router.push("/reset/new");
    } catch (caught) {
      setError(describeAuthError(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen
      footer={
        <>
          <Button label="Send a code" onPress={submit} loading={busy} testID="reset-submit" />
          <TextLink
            label="I changed my number or cannot get in"
            onPress={() => router.push({ pathname: "/unavailable", params: { topic: "locked-out" } })}
          />
        </>
      }
    >
      <Stack.Screen options={{ title: "Reset your password" }} />
      <BackButton fallback="/login" />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>Reset your password</Title>
        <Txt color={colors.muted}>
          Enter the mobile number you log in with. We will text it a 6 digit code.
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 14 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <PhoneField value={phone} onChangeText={setPhone} error={phoneError} autoFocus />
        <Notice tone="neutral">
          For your safety, resetting your password signs you out on every device, and for 24 hours
          withdrawals only go to accounts you have used before.
        </Notice>
      </View>
    </Screen>
  );
}
