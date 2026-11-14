import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { isValidEmail } from "@/auth/password";
import { toE164 } from "@/auth/phone";
import { describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { PhoneField, TextField } from "@/components/Field";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

export default function SignupStart() {
  const router = useRouter();
  const { client, setSignup } = useAuth();
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const e164 = toE164(phone);
  const emailOk = isValidEmail(email);
  const phoneError = submitted && !e164 ? "Enter a mobile number like 772 123 456." : null;
  const emailError = submitted && !emailOk ? "Enter an email address like name@example.com." : null;
  const termsError = submitted && !agreed;

  const submit = async () => {
    setSubmitted(true);
    setError(null);
    if (!e164 || !emailOk || !agreed) {
      return;
    }
    setBusy(true);
    try {
      const challenge = await client.startSignup({
        phone: e164,
        email: email.trim(),
        inviteCode: inviteCode.trim() || undefined,
      });
      setSignup(challenge);
      router.push("/signup/code");
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
          <Button label="Text me a code" onPress={submit} loading={busy} testID="signup-submit" />
          <TextLink label="I already have an account" onPress={() => router.replace("/login")} />
          <TextLink
            label="Open an account for a company or group"
            onPress={() => router.push({ pathname: "/unavailable", params: { topic: "group" } })}
          />
        </>
      }
    >
      <Stack.Screen options={{ title: "Create your account" }} />
      <BackButton fallback="/welcome" />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>Create your account</Title>
        <Txt color={colors.muted}>Start with your phone number. No forms, no branch visit.</Txt>
      </View>

      <View style={{ marginTop: 22, gap: 14 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <PhoneField
          value={phone}
          onChangeText={setPhone}
          hint="Use the number your mobile money is on."
          error={phoneError}
        />
        <TextField
          label="Email"
          value={email}
          onChangeText={setEmail}
          hint="For statements and receipts."
          error={emailError}
          keyboardType="email-address"
          inputMode="email"
          autoCapitalize="none"
          autoCorrect={false}
          autoComplete="email"
          testID="email-input"
        />
        <TextField
          label="Invite code, if you have one"
          value={inviteCode}
          onChangeText={setInviteCode}
          placeholder="Optional"
          autoCapitalize="characters"
          autoCorrect={false}
        />
        <Pressable
          accessibilityRole="checkbox"
          aria-checked={agreed}
          onPress={() => setAgreed((current) => !current)}
          style={styles.terms}
          testID="terms-checkbox"
        >
          <View
            style={[
              styles.box,
              agreed && styles.boxChecked,
              termsError && { borderColor: colors.danger },
            ]}
          >
            {agreed ? <Icon name="check" size={16} color={colors.white} strokeWidth={3} /> : null}
          </View>
          <Txt size={fontSizes.sm} style={{ flex: 1 }} color={termsError ? colors.danger : colors.ink}>
            I agree to the terms and the privacy notice, and I am 18 or older.
          </Txt>
        </Pressable>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  terms: { flexDirection: "row", gap: 12, alignItems: "flex-start", minHeight: 44 },
  box: {
    width: 22,
    height: 22,
    borderRadius: 6,
    borderWidth: 2,
    borderColor: colors.black,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  boxChecked: { backgroundColor: colors.black },
});
