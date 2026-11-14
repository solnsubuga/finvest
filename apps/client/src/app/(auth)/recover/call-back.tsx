import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { toE164 } from "@/auth/phone";
import { describeAuthError, RecoveryReason } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Field, PhoneField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

const REASONS: readonly { value: RecoveryReason; label: string }[] = [
  { value: "lost_phone", label: "I lost my phone or SIM card" },
  { value: "new_number", label: "I changed my number" },
  { value: "no_codes", label: "My codes do not arrive" },
  { value: "other", label: "Something else" },
];

function isReason(value: unknown): value is RecoveryReason {
  return REASONS.some((reason) => reason.value === value);
}

/**
 * Locked out, the call back: the number on the account, a number that works now, and what
 * happened. There is no free text, so nothing personal is kept beyond the two numbers.
 */
export default function AskForCallBack() {
  const router = useRouter();
  const { client } = useAuth();
  const params = useLocalSearchParams<{ reason?: string }>();
  const [reason, setReason] = useState<RecoveryReason>(isReason(params.reason) ? params.reason : "lost_phone");
  const [phone, setPhone] = useState("");
  const [contact, setContact] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accountPhone = toE164(phone);
  const contactPhone = toE164(contact);
  const phoneError = submitted && !accountPhone ? "Enter the number on your account, like 772 123 456." : null;
  const contactError = submitted && !contactPhone ? "Enter a number we can call you on now." : null;

  const submit = async () => {
    setSubmitted(true);
    setError(null);
    if (!accountPhone || !contactPhone) {
      return;
    }
    setBusy(true);
    try {
      await client.requestRecovery({ phone: accountPhone, contactPhone, reason });
      router.replace("/recover/sent");
    } catch (caught) {
      setError(describeAuthError(caught));
      setBusy(false);
    }
  };

  return (
    <Screen footer={<Button label="Ask for a call" onPress={submit} loading={busy} testID="call-back-submit" />}>
      <Stack.Screen options={{ title: "Ask us to call you" }} />
      <BackButton fallback="/recover" />
      <View style={{ marginTop: 28, gap: 10 }}>
        <Title>Ask us to call you</Title>
        <Txt color={colors.muted}>
          Our team calls you, checks it is you against your ID, and helps you back in. If your number
          changed, a second person approves the move before it happens.
        </Txt>
      </View>

      <View style={{ marginTop: 20, gap: 14 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <Field label="What happened">
          <View accessibilityRole="radiogroup" style={styles.reasons}>
            {REASONS.map((option) => {
              const selected = option.value === reason;
              return (
                <Pressable
                  key={option.value}
                  accessibilityRole="radio"
                  aria-checked={selected}
                  onPress={() => setReason(option.value)}
                  testID={`reason-${option.value}`}
                  style={({ pressed }) => [styles.reason, selected && styles.reasonOn, pressed && { opacity: 0.7 }]}
                >
                  <View style={[styles.dot, selected && styles.dotOn]} />
                  <Txt size={fontSizes.sm} weight="semibold">
                    {option.label}
                  </Txt>
                </Pressable>
              );
            })}
          </View>
        </Field>
        <PhoneField
          label="Mobile number on your account"
          value={phone}
          onChangeText={setPhone}
          error={phoneError}
          testID="account-phone-input"
        />
        <PhoneField
          label="Number to call you on"
          hint="A number that works now. It can be the same one if you still have it."
          value={contact}
          onChangeText={setContact}
          error={contactError}
          testID="contact-phone-input"
        />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  reasons: { gap: 8 },
  reason: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    minHeight: 48,
    paddingHorizontal: 14,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  reasonOn: { borderColor: colors.black, borderWidth: 1.5 },
  dot: { width: 18, height: 18, borderRadius: 9, borderWidth: 2, borderColor: colors.borderStrong },
  dotOn: { borderColor: colors.black, borderWidth: 6 },
});
