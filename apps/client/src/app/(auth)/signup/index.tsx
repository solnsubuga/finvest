import { useState } from "react";
import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { isValidEmail } from "@/auth/password";
import { toE164 } from "@/auth/phone";
import { describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { PhoneField, TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { TermsAgreement } from "@/components/TermsAgreement";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { clearIntent, writeIntent } from "@/organisations/intent";
import { colors } from "@/theme";

export default function SignupStart() {
  const router = useRouter();
  const { client, setSignup } = useAuth();
  // Came from "Open an account for a company or group": this is the applicant's own account.
  const forOrganisation = useLocalSearchParams<{ for?: string }>().for === "organisation";
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
      // Home leads on to the company or group application once the ID check is approved.
      if (forOrganisation) await writeIntent(e164);
      else await clearIntent();
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
          {forOrganisation ? null : (
            <TextLink
              label="Open an account for a company or group"
              onPress={() => router.push("/signup/organisation")}
              testID="signup-organisation"
            />
          )}
        </>
      }
    >
      <Stack.Screen options={{ title: "Create your account" }} />
      <BackButton fallback={forOrganisation ? "/signup/organisation" : "/welcome"} />
      <View style={{ marginTop: 28, gap: 10 }}>
        {forOrganisation ? <Eyebrow>Step 1 of 3, your own account</Eyebrow> : null}
        <Title>Create your account</Title>
        <Txt color={colors.muted}>
          {forOrganisation
            ? "Your own account comes first. Once your ID is checked, you apply for the company or group from Home."
            : "Start with your phone number. No forms, no branch visit."}
        </Txt>
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
        <TermsAgreement agreed={agreed} onChange={setAgreed} invalid={termsError} />
      </View>
    </Screen>
  );
}
