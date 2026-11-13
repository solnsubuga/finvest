import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { maskE164 } from "@/auth/phone";
import { describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

export default function LoginVerify() {
  const router = useRouter();
  const { client, login, setLogin, setSession } = useAuth();
  const [busy, setBusy] = useState<"passkey" | "code" | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!login) {
    return <Redirect href="/login" />;
  }

  const hasPasskey = login.methods.includes("passkey");

  const usePasskey = async () => {
    setBusy("passkey");
    setError(null);
    try {
      const session = await client.verifyWithPasskey(login.loginId);
      setLogin(null);
      setSession(session);
    } catch (caught) {
      setError(describeAuthError(caught));
      setBusy(null);
    }
  };

  const sendCode = async () => {
    setBusy("code");
    setError(null);
    try {
      await client.sendLoginCode(login.loginId);
      router.push("/login/code");
    } catch (caught) {
      setError(describeAuthError(caught));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Screen
      footer={
        <TextLink
          label="I cannot use any of these"
          onPress={() => router.push({ pathname: "/unavailable", params: { topic: "locked-out" } })}
        />
      }
    >
      <Stack.Screen options={{ title: "Confirm it is you" }} />
      <BackButton fallback="/login" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Step 2 of 2</Eyebrow>
        <Title>Confirm it is you</Title>
        <Txt color={colors.muted}>
          {hasPasskey
            ? "Your password was correct. Now use the passkey saved on this device."
            : "Your password was correct. You have no passkey yet, so we will send you a code."}
        </Txt>
      </View>

      {error ? (
        <View style={{ marginTop: 16 }}>
          <Notice tone="danger">{error}</Notice>
        </View>
      ) : null}

      {hasPasskey ? (
        <View style={styles.passkey}>
          <View style={styles.passkeyBadge}>
            <Icon name="key" size={30} color={colors.black} />
          </View>
          <Txt size={18} weight="extrabold" color={colors.white} center style={{ marginTop: 14 }}>
            Your passkey
          </Txt>
          <Txt size={fontSizes.sm} color={colors.onBlackMuted} center style={{ marginTop: 4 }}>
            Your device will ask for your fingerprint, face or screen lock.
          </Txt>
          <View style={{ alignSelf: "stretch", marginTop: 16 }}>
            <Button
              label="Use passkey"
              variant="lime"
              height={52}
              onPress={usePasskey}
              loading={busy === "passkey"}
              disabled={busy === "code"}
              testID="use-passkey"
            />
          </View>
        </View>
      ) : null}

      <View style={{ marginTop: 22, gap: 8 }}>
        <Eyebrow>{hasPasskey ? "Other ways" : "How to confirm"}</Eyebrow>
        <Card padded={false}>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: busy !== null, busy: busy === "code" }}
            disabled={busy !== null}
            onPress={sendCode}
            style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
            testID="send-code"
          >
            <View style={{ flex: 1 }}>
              <Txt weight="bold">Send me a code</Txt>
              <Txt size={fontSizes.sm} color={colors.muted}>
                {`By SMS to ${maskE164(login.phone)}`}
              </Txt>
            </View>
            <Icon name="chevron" size={16} strokeWidth={2.2} />
          </Pressable>
        </Card>
      </View>
      <View style={{ marginTop: 14 }}>
        <Notice tone="warning">
          Signing in with a code instead of a passkey limits withdrawals to accounts you have used
          before for 24 hours.
        </Notice>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  passkey: {
    marginTop: 22,
    backgroundColor: colors.black,
    borderRadius: 24,
    paddingVertical: 22,
    paddingHorizontal: 20,
    alignItems: "center",
  },
  passkeyBadge: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
    paddingHorizontal: 16,
    minHeight: 56,
  },
});
