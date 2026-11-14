import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { isValidEmail } from "@/auth/password";
import { toE164 } from "@/auth/phone";
import { describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { PasswordField, PhoneField, TextField } from "@/components/Field";
import { Icon } from "@/components/Icon";
import { LogoMark } from "@/components/Logo";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

export default function LoginStart() {
  const router = useRouter();
  const { client, setLogin } = useAuth();
  const params = useLocalSearchParams<{ with?: string }>();
  // The design's "Use email instead": a proved address in place of the number.
  const [byEmail, setByEmail] = useState(params.with === "email");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const e164 = toE164(phone);
  const emailOk = isValidEmail(email);
  const phoneError = submitted && !byEmail && !e164 ? "Enter a mobile number like 772 123 456." : null;
  const emailError =
    submitted && byEmail && !emailOk ? "Enter an email address like name@example.com." : null;
  const passwordError = submitted && password.length === 0 ? "Enter your password." : null;

  const switchTo = (email: boolean) => {
    setByEmail(email);
    setSubmitted(false);
    setError(null);
  };

  const submit = async () => {
    setSubmitted(true);
    setError(null);
    if ((byEmail ? !emailOk : !e164) || password.length === 0) {
      return;
    }
    setBusy(true);
    try {
      setLogin(
        await client.startLogin(byEmail ? { email } : { phone: e164 as string }, password),
      );
      setPassword("");
      router.push("/login/verify");
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
          <Button label="Continue" onPress={submit} loading={busy} testID="login-submit" />
          <TextLink label="New here? Create an account" onPress={() => router.replace("/signup")} />
          <TextLink
            label="I changed my number or cannot get in"
            onPress={() => router.push("/recover")}
          />
        </>
      }
    >
      <Stack.Screen options={{ title: "Log in" }} />
      <BackButton fallback="/welcome" />
      <View style={{ marginTop: 28 }}>
        <LogoMark size={52} />
      </View>
      <View style={{ marginTop: 20, gap: 10 }}>
        <Title>Welcome back</Title>
        <Txt color={colors.muted}>
          {byEmail
            ? "Sign in with your email address and password."
            : "Sign in with your mobile number and password."}
        </Txt>
      </View>

      <View style={{ marginTop: 20, gap: 14 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        {byEmail ? (
          <TextField
            label="Email"
            value={email}
            onChangeText={setEmail}
            hint="The address you confirmed by the link we emailed you."
            error={emailError}
            keyboardType="email-address"
            inputMode="email"
            autoCapitalize="none"
            autoCorrect={false}
            autoComplete="email"
            testID="email-input"
          />
        ) : (
          <PhoneField value={phone} onChangeText={setPhone} error={phoneError} />
        )}
        <PasswordField
          label="Password"
          value={password}
          onChangeText={setPassword}
          error={passwordError}
          onSubmitEditing={submit}
          testID="password-input"
        />
      </View>
      <View style={styles.links}>
        <TextLink
          label={byEmail ? "Use mobile number instead" : "Use email instead"}
          onPress={() => switchTo(!byEmail)}
          testID="switch-identifier"
        />
        <TextLink label="Forgot password?" onPress={() => router.push("/reset")} />
      </View>
      <View style={{ marginTop: 8 }}>
        <Card padded={false}>
          <View style={styles.twoStep}>
            <View style={styles.keyBadge}>
              <Icon name="key" size={18} color={colors.black} />
            </View>
            <Txt size={fontSizes.sm} style={{ flex: 1 }}>
              <Txt size={fontSizes.sm} weight="bold">
                Two steps, every time.
              </Txt>{" "}
              After your password we confirm it is you with your passkey, your authenticator app
              or a code by SMS or email.
            </Txt>
          </View>
        </Card>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  links: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  twoStep: {
    flexDirection: "row",
    gap: 12,
    alignItems: "flex-start",
    paddingVertical: 14,
    paddingHorizontal: 16,
  },
  keyBadge: {
    width: 36,
    height: 36,
    borderRadius: 12,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
});
