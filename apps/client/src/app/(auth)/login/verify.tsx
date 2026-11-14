import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { maskE164 } from "@/auth/phone";
import { describeAuthError, SecondStep } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon, IconName } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

type MethodCopy = {
  icon: IconName;
  /** Heading on the black card when this method is offered first. */
  title: string;
  detail: string;
  action: string;
  /** Row label when this method is one of the other ways. */
  row: string;
  intro: string;
};

function copyFor(method: SecondStep, maskedPhone: string): MethodCopy {
  switch (method) {
    case "passkey":
      return {
        icon: "key",
        title: "Your passkey",
        detail: "Your device will ask for your fingerprint, face or screen lock.",
        action: "Use passkey",
        row: "Use my passkey",
        intro: "Your password was correct. Now use the passkey saved on this device.",
      };
    case "authenticator":
      return {
        icon: "phone",
        title: "Your authenticator app",
        detail: "Open the app and find the 6 digit code for Finvest.",
        action: "Enter the code from my app",
        row: "Use my authenticator app",
        intro: "Your password was correct. Now enter a code from your authenticator app.",
      };
    case "code":
      return {
        icon: "message",
        title: "A code by SMS",
        detail: `We will send a 6 digit code to ${maskedPhone}.`,
        action: "Send me a code",
        row: "Send me a code",
        intro: "Your password was correct. Now we will send a code to your phone.",
      };
  }
}

/**
 * Log in, step 2. Shows every way this client has set up to confirm it is
 * them. The strongest one is offered up front, and any of the others can be
 * chosen instead.
 */
export default function LoginVerify() {
  const router = useRouter();
  const { client, login, setLogin, setSession } = useAuth();
  const [busy, setBusy] = useState<SecondStep | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!login) {
    return <Redirect href="/login" />;
  }

  const maskedPhone = maskE164(login.phone);
  const [primary = "code", ...others] = login.methods;
  const primaryCopy = copyFor(primary, maskedPhone);

  const choose = async (method: SecondStep) => {
    setError(null);
    if (method === "authenticator") {
      router.push("/login/authenticator");
      return;
    }
    setBusy(method);
    try {
      if (method === "passkey") {
        const session = await client.verifyWithPasskey(login.loginId);
        setLogin(null);
        setSession(session);
        return;
      }
      await client.sendLoginCode(login.loginId);
      router.push("/login/code");
      setBusy(null);
    } catch (caught) {
      setError(describeAuthError(caught));
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
        <Txt color={colors.muted}>{primaryCopy.intro}</Txt>
      </View>

      {error ? (
        <View style={{ marginTop: 16 }}>
          <Notice tone="danger">{error}</Notice>
        </View>
      ) : null}

      <View style={styles.primary}>
        <View style={styles.badge}>
          <Icon name={primaryCopy.icon} size={30} color={colors.black} />
        </View>
        <Txt size={18} weight="extrabold" color={colors.white} center style={{ marginTop: 14 }}>
          {primaryCopy.title}
        </Txt>
        <Txt size={fontSizes.sm} color={colors.onBlackMuted} center style={{ marginTop: 4 }}>
          {primaryCopy.detail}
        </Txt>
        <View style={{ alignSelf: "stretch", marginTop: 16 }}>
          <Button
            label={primaryCopy.action}
            variant="lime"
            height={52}
            onPress={() => choose(primary)}
            loading={busy === primary}
            disabled={busy !== null && busy !== primary}
            testID={`use-${primary}`}
          />
        </View>
      </View>

      {others.length > 0 ? (
        <View style={{ marginTop: 22, gap: 8 }}>
          <Eyebrow>Or use another way</Eyebrow>
          <Card padded={false}>
            {others.map((method, index) => {
              const copy = copyFor(method, maskedPhone);
              return (
                <Pressable
                  key={method}
                  accessibilityRole="button"
                  aria-disabled={busy !== null}
                  aria-busy={busy === method}
                  disabled={busy !== null}
                  onPress={() => choose(method)}
                  style={({ pressed }) => [
                    styles.row,
                    index > 0 && styles.rowDivider,
                    pressed && { opacity: 0.7 },
                  ]}
                  testID={`use-${method}`}
                >
                  <View style={styles.rowIcon}>
                    <Icon name={copy.icon} size={18} color={colors.black} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Txt weight="bold">{copy.row}</Txt>
                    <Txt size={fontSizes.sm} color={colors.muted}>
                      {method === "code" ? `By SMS to ${maskedPhone}` : copy.detail}
                    </Txt>
                  </View>
                  <Icon name="chevron" size={16} strokeWidth={2.2} />
                </Pressable>
              );
            })}
          </Card>
        </View>
      ) : null}

      <View style={{ marginTop: 14 }}>
        <Notice tone="warning">
          {login.methods.length > 1
            ? "Signing in with an SMS code limits withdrawals to accounts you have used before for 24 hours. A passkey or an authenticator app does not."
            : "Signing in with an SMS code limits withdrawals to accounts you have used before for 24 hours. Add a passkey or an authenticator app to avoid this."}
        </Notice>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  primary: {
    marginTop: 22,
    backgroundColor: colors.black,
    borderRadius: 24,
    paddingVertical: 22,
    paddingHorizontal: 20,
    alignItems: "center",
  },
  badge: {
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
    minHeight: 60,
  },
  rowDivider: { borderTopWidth: 1, borderTopColor: colors.divider },
  rowIcon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
});
