import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { isValidEmail } from "@/auth/password";
import { toE164 } from "@/auth/phone";
import { AuthError, describeAuthError } from "@/auth/types";
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
  const { client, setLogin, setSession } = useAuth();
  const params = useLocalSearchParams<{ with?: string }>();
  // The design's "Use email instead": the account's email address in place of the number.
  const [byEmail, setByEmail] = useState(params.with === "email");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [passkeyBusy, setPasskeyBusy] = useState(false);
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

  // A passkey alone logs in: the device checks the fingerprint, face or screen lock, so it
  // counts as both steps. The passkey the client picks says whose account it is.
  const logInWithPasskey = async () => {
    setSubmitted(false);
    setError(null);
    setPasskeyBusy(true);
    try {
      const session = await client.logInWithPasskey();
      setPassword("");
      setLogin(null);
      setSession(session);
    } catch (caught) {
      setError(passkeyProblem(caught));
      setPasskeyBusy(false);
    }
  };

  return (
    <Screen
      footer={
        <>
          <Button
            label="Continue"
            onPress={submit}
            loading={busy}
            disabled={passkeyBusy}
            testID="login-submit"
          />
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
            hint="The email address on your Finvest account."
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
      {client.canLogInWithPasskey() ? (
        <View style={{ marginTop: 4, gap: 14 }}>
          <View style={styles.or}>
            <View style={styles.rule} />
            <Txt size={fontSizes.sm} color={colors.muted}>
              or
            </Txt>
            <View style={styles.rule} />
          </View>
          <Button
            label="Log in with a passkey"
            icon="key"
            variant="secondary"
            onPress={logInWithPasskey}
            loading={passkeyBusy}
            disabled={busy}
            testID="login-passkey"
          />
        </View>
      ) : null}
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
              or a code by SMS or email. A passkey on this device does both at once.
            </Txt>
          </View>
        </Card>
      </View>
    </Screen>
  );
}

/** What to say when a passkey log in did not finish. Closing the prompt is not a failure. */
function passkeyProblem(caught: unknown): string {
  if (caught instanceof AuthError && caught.code === "cancelled") {
    return "No passkey was used. Try again, or log in with your number and password.";
  }
  if (caught instanceof AuthError && caught.code === "passkey_failed") {
    return "That passkey is not saved to a Finvest account. Log in with your number and password, then add a passkey in Sign in and security.";
  }
  return describeAuthError(caught);
}

const styles = StyleSheet.create({
  or: { flexDirection: "row", alignItems: "center", gap: 12 },
  rule: { flex: 1, height: 1, backgroundColor: colors.border },
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
