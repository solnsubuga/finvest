import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";
import { Href, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { maskE164 } from "@/auth/phone";
import { SecondStep } from "@/auth/types";
import { useSecondSteps } from "@/auth/useSecondSteps";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon, IconName } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors, fontSizes, radii } from "@/theme";

type Row = {
  method: SecondStep;
  icon: IconName;
  title: string;
  on: string;
  off: string;
  /** Where set-up starts. SMS has none: it is on for every account. */
  setup?: Href;
  action?: string;
  /** Where to manage it once it is on, and what the link says. */
  manage?: Href;
  manageAction?: string;
};

function rowsFor(maskedPhone: string): Row[] {
  return [
    {
      method: "passkey",
      icon: "key",
      title: "Passkey",
      on: "Your fingerprint, face or screen lock.",
      off: "Your fingerprint, face or screen lock. The safest way.",
      setup: "/security/passkey",
      action: "Add",
      manage: "/security/passkeys",
      manageAction: "Manage",
    },
    {
      method: "authenticator",
      icon: "phone",
      title: "Authenticator app",
      on: "A 6 digit code from the app on your phone.",
      off: "A 6 digit code from an app on your phone. No SMS to wait for.",
      setup: "/security/authenticator",
      action: "Set up",
      manage: "/security/authenticator-off",
      manageAction: "Turn off",
    },
    {
      method: "code",
      icon: "message",
      title: "Code by SMS",
      on: `Sent to ${maskedPhone}. Always available.`,
      off: `Sent to ${maskedPhone}.`,
    },
  ];
}

/**
 * Profile, Sign in and security: the ways this client can confirm a log in
 * after the password, and where to add the ones they do not have yet.
 */
export default function Security() {
  const router = useRouter();
  const { session } = useAuth();
  const steps = useSecondSteps();

  if (!session) {
    return null;
  }

  const methods = steps.status === "ready" ? steps.methods : [];
  const onlySms = steps.status === "ready" && methods.length === 1;

  return (
    <Screen>
      <Stack.Screen options={{ title: "Sign in and security" }} />
      <BackButton fallback="/profile" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Profile</Eyebrow>
        <Title>Sign in and security</Title>
        <Txt color={colors.muted}>
          After your password, you confirm it is you with one of these. You can use any one that is
          on, so set up more than one and you are never locked out.
        </Txt>
      </View>

      {steps.status === "loading" ? (
        <View style={{ marginTop: 40 }}>
          <ActivityIndicator accessibilityLabel="Loading" color={colors.black} />
        </View>
      ) : null}

      {steps.status === "error" ? (
        <View style={{ marginTop: 22, gap: 12 }}>
          <Notice tone="danger">{steps.message}</Notice>
          <Button label="Try again" onPress={steps.reload} height={52} />
        </View>
      ) : null}

      {steps.status === "ready" ? (
        <View style={{ marginTop: 22, gap: 14 }}>
          <Card padded={false}>
            {rowsFor(maskE164(session.phone)).map((row, index) => {
              const isOn = methods.includes(row.method);
              const target = isOn ? row.manage : row.setup;
              const actionLabel = isOn ? row.manageAction : row.action;
              const content = (
                <>
                  <View style={[styles.rowIcon, isOn && { backgroundColor: colors.black }]}>
                    <Icon name={row.icon} size={18} color={isOn ? colors.lime : colors.black} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Txt weight="bold">{row.title}</Txt>
                    <Txt size={fontSizes.sm} color={colors.muted}>
                      {isOn ? row.on : row.off}
                    </Txt>
                  </View>
                  {isOn ? (
                    <View style={styles.add}>
                      <View style={styles.on}>
                        <Txt size={12} weight="bold">
                          On
                        </Txt>
                      </View>
                      {target ? <Icon name="chevron" size={16} strokeWidth={2.2} /> : null}
                    </View>
                  ) : (
                    <View style={styles.add}>
                      <Txt size={fontSizes.sm} weight="bold">
                        {row.action ?? ""}
                      </Txt>
                      <Icon name="chevron" size={16} strokeWidth={2.2} />
                    </View>
                  )}
                </>
              );
              const style = [styles.row, index > 0 && styles.rowDivider];
              return target ? (
                <Pressable
                  key={row.method}
                  accessibilityRole="button"
                  accessibilityLabel={`${actionLabel} ${row.title.toLowerCase()}${isOn ? ": on" : ""}`}
                  onPress={() => router.push(target)}
                  style={({ pressed }) => [...style, pressed && { opacity: 0.7 }]}
                  testID={isOn ? `on-${row.method}` : `setup-${row.method}`}
                >
                  {content}
                </Pressable>
              ) : (
                <View
                  key={row.method}
                  accessible
                  accessibilityLabel={`${row.title}: on`}
                  style={style}
                  testID={`on-${row.method}`}
                >
                  {content}
                </View>
              );
            })}
          </Card>

          <Card padded={false}>
            <Pressable
              accessibilityRole="link"
              onPress={() => router.push("/security/devices")}
              style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
              testID="open-devices"
            >
              <View style={styles.rowIcon}>
                <Icon name="phone" size={18} color={colors.black} />
              </View>
              <View style={{ flex: 1 }}>
                <Txt weight="bold">Devices</Txt>
                <Txt size={fontSizes.sm} color={colors.muted}>
                  Where you are signed in. Sign out a device you do not know.
                </Txt>
              </View>
              <Icon name="chevron" size={16} strokeWidth={2.2} />
            </Pressable>
          </Card>

          {session.restricted ? (
            <Notice tone="warning">
              You signed in with an SMS code, so withdrawals only go to accounts you have used before
              until 24 hours have passed. Adding a passkey or an authenticator app now does not end
              that early. It counts from your next log in.
            </Notice>
          ) : onlySms ? (
            <Notice tone="warning">
              With only SMS codes, every log in limits withdrawals to accounts you have used before
              for 24 hours. Add a passkey or an authenticator app to avoid this.
            </Notice>
          ) : null}
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 14,
    paddingHorizontal: 16,
    minHeight: 68,
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
  on: {
    backgroundColor: colors.lime,
    borderRadius: radii.pill,
    paddingVertical: 4,
    paddingHorizontal: 12,
  },
  add: { flexDirection: "row", alignItems: "center", gap: 4 },
});
