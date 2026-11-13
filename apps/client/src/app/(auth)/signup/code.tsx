import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164 } from "@/auth/phone";
import { AuthError, describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Card, Notice, StepProgress } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

const NEXT_STEPS = [
  {
    title: "Confirm your number.",
    body: "It also becomes your mobile money link for payments and withdrawals.",
  },
  {
    title: "Create a password and a PIN.",
    body: "The password signs you in. The PIN opens the app and approves payments.",
  },
  {
    title: "Add a passkey, then scan your National ID.",
    body: "Takes about three minutes.",
  },
];

export default function SignupCode() {
  const router = useRouter();
  const { client, signup, setTicket } = useAuth();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [taken, setTaken] = useState(false);

  if (!signup) {
    return <Redirect href="/signup" />;
  }

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      setTicket(await client.confirmSignupCode(signup.challengeId, code));
      router.push("/signup/password");
    } catch (caught) {
      setError(describeAuthError(caught));
      setTaken(caught instanceof AuthError && caught.code === "phone_taken");
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen
      footer={
        taken ? (
          <Button label="Log in" onPress={() => router.replace("/login")} testID="go-to-login" />
        ) : (
          <Button
            label="Continue"
            onPress={submit}
            loading={busy}
            disabled={code.length < CODE_LENGTH}
            testID="code-submit"
          />
        )
      }
    >
      <Stack.Screen options={{ title: "Enter your code" }} />
      <View style={styles.top}>
        <BackButton fallback="/signup" />
        <StepProgress step={1} of={4} />
      </View>
      <View style={{ marginTop: 36, gap: 12 }}>
        <Title>Enter the code we sent you</Title>
        <Txt color={colors.muted}>
          We sent a 6 digit code by SMS to{" "}
          <Txt weight="bold">{formatE164(signup.phone)}</Txt>.
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 12 }}>
        <CodeInput
          value={code}
          onChangeText={(next) => {
            setCode(next);
            setError(null);
          }}
          invalid={Boolean(error) && !taken}
        />
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <View style={{ alignItems: "flex-start" }}>
          <TextLink
            label="Change number"
            onPress={() => (router.canGoBack() ? router.back() : router.replace("/signup"))}
          />
        </View>
      </View>
      <View style={{ marginTop: 16 }}>
        <Card>
          <View style={{ gap: 14 }}>
            <Eyebrow>What happens next</Eyebrow>
            {NEXT_STEPS.map((step, index) => (
              <View key={step.title} style={styles.step}>
                <View
                  style={[
                    styles.stepNumber,
                    { backgroundColor: index === 0 ? colors.black : colors.border },
                  ]}
                >
                  <Txt size={13} weight="bold" color={index === 0 ? colors.white : colors.ink}>
                    {String(index + 1)}
                  </Txt>
                </View>
                <Txt size={14} lineHeight={20} style={{ flex: 1 }}>
                  <Txt size={14} weight="bold" lineHeight={20}>
                    {step.title}
                  </Txt>{" "}
                  {step.body}
                </Txt>
              </View>
            ))}
          </View>
        </Card>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: "row", alignItems: "center", gap: 16 },
  step: { flexDirection: "row", gap: 12, alignItems: "flex-start" },
  stepNumber: {
    width: 26,
    height: 26,
    borderRadius: 13,
    alignItems: "center",
    justifyContent: "center",
  },
});
