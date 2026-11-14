import { useState } from "react";
import { View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { codeTargetFor } from "@/auth/codeTarget";
import { describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

export default function LoginCode() {
  const router = useRouter();
  const { client, login, setLogin, setSession } = useAuth();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!login) {
    return <Redirect href="/login" />;
  }
  const target = codeTargetFor(login, login.codeSentBy);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const session = await client.verifyWithCode(login.loginId, code);
      setLogin(null);
      setSession(session);
    } catch (caught) {
      setError(describeAuthError(caught));
      setCode("");
      setBusy(false);
    }
  };

  return (
    <Screen
      footer={
        <>
          <Button
            label="Log in"
            onPress={submit}
            loading={busy}
            disabled={code.length < CODE_LENGTH}
            testID="code-submit"
          />
          {login.methods.length > 1 || login.email ? (
            <TextLink
              label="Use another way"
              onPress={() =>
                router.canGoBack() ? router.back() : router.replace("/login/verify")
              }
            />
          ) : null}
        </>
      }
    >
      <Stack.Screen options={{ title: "Enter your code" }} />
      <BackButton fallback="/login/verify" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Step 2 of 2</Eyebrow>
        <Title>Enter the code we sent you</Title>
        <Txt color={colors.muted}>
          {target.channel === "email"
            ? `We emailed a 6 digit code to ${target.where}. It can take a minute to arrive.`
            : `We sent a 6 digit code by SMS to ${target.where}.`}
        </Txt>
      </View>
      <View style={{ marginTop: 24, gap: 12 }}>
        <CodeInput
          value={code}
          onChangeText={(next) => {
            setCode(next);
            setError(null);
          }}
          invalid={Boolean(error)}
        />
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <Notice tone="warning">
          Because you are using a code, withdrawals only go to accounts you have used before for the
          next 24 hours.
        </Notice>
      </View>
    </Screen>
  );
}
