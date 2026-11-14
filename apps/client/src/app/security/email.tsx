import { useCallback, useState } from "react";
import { View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { ConfirmItIsYou, useConfirmedChange } from "@/components/ConfirmItIsYou";
import { Done } from "@/components/Done";
import { TextField } from "@/components/Field";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors } from "@/theme";

/** Sign in and security: change the email address, after a passkey or the authenticator app. */
export default function ChangeEmail() {
  const router = useRouter();
  const { client, session, setSession } = useAuth();
  const [email, setEmail] = useState("");
  const [saved, setSaved] = useState<string | null>(null);

  const change = useCallback(() => client.changeEmail(email.trim()), [client, email]);
  const onDone = useCallback(
    (address: string) => {
      setSaved(address);
      if (session) setSession({ ...session, email: address, emailVerified: false });
    },
    [session, setSession],
  );
  const { run, send, busy, error, confirming, setConfirming } = useConfirmedChange(change, onDone);

  if (!session) return null;
  if (saved) {
    return (
      <Done
        title="Check your inbox"
        detail={`We emailed a link to ${saved}. Open it within 24 hours to confirm the address is yours. Until then we send statements and receipts nowhere. We told your old address too.`}
        action="Back to security"
        onPress={() => router.dismissTo("/security")}
      />
    );
  }

  return (
    <Screen
      signedIn
      footer={
        confirming ? null : (
          <Button label="Save email" onPress={run} loading={busy} disabled={!/^\S+@\S+$/.test(email.trim())} testID="email-change" />
        )
      }
    >
      <Stack.Screen options={{ title: "Change email" }} />
      <BackButton fallback="/security" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Sign in and security</Eyebrow>
        <Title>{session.email ? "Change your email" : "Add your email"}</Title>
        <Txt color={colors.muted}>
          {session.email
            ? `Now ${session.email}. We send statements and receipts only to an address you have confirmed.`
            : "We send statements and receipts only to an address you have confirmed."}
        </Txt>
      </View>
      <View style={{ marginTop: 20, gap: 14 }}>
        {confirming ? <ConfirmItIsYou onConfirmed={send} onCancel={() => setConfirming(false)} /> : null}
        {error ? <Notice tone="danger">{error}</Notice> : null}
        <TextField
          label="New email address"
          value={email}
          onChangeText={setEmail}
          keyboardType="email-address"
          autoCapitalize="none"
          autoCorrect={false}
          autoComplete="email"
          maxLength={254}
          testID="email-input"
        />
      </View>
    </Screen>
  );
}
