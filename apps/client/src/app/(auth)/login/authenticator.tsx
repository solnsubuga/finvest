import { useState } from "react";
import { View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

/** Log in, step 2, with the code from an authenticator app. */
export default function LoginAuthenticator() {
  const router = useRouter();
  const { client, login, setLogin, setSession } = useAuth();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!login || !login.methods.includes("authenticator")) {
    return <Redirect href="/login" />;
  }

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const session = await client.verifyWithAuthenticator(login.loginId, code);
      setLogin(null);
      setSession(session);
    } catch (caught) {
      setError(describeAuthError(caught));
      setCode("");
      setBusy(false);
    }
  };

  const back = () => (router.canGoBack() ? router.back() : router.replace("/login/verify"));

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
          {login.methods.length > 1 ? (
            <TextLink label="Use another way" onPress={back} />
          ) : null}
        </>
      }
    >
      <Stack.Screen options={{ title: "Authenticator code" }} />
      <BackButton fallback="/login/verify" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Step 2 of 2</Eyebrow>
        <Title>Enter the code from your app</Title>
        <Txt color={colors.muted}>
          Open your authenticator app and type the 6 digit code it shows for Finvest. The code
          changes every 30 seconds.
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
      </View>
    </Screen>
  );
}
