import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Done } from "@/components/Done";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

const reasons = [
  ["It is your second step", "After your password, the passkey proves it is really you."],
  [
    "It cannot be stolen by a fake site",
    "There is no code to read out, so nobody can trick you into sharing it.",
  ],
  ["It works without network", "No waiting for an SMS that never arrives."],
] as const;

/** Add a passkey, from the design canvas. The device does the asking. */
export default function AddPasskey() {
  const router = useRouter();
  const { client } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState(false);

  const leave = () => (router.canGoBack() ? router.back() : router.replace("/security"));

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await client.addPasskey();
      setAdded(true);
    } catch (caught) {
      setError(describeAuthError(caught));
      setBusy(false);
    }
  };

  if (added) {
    return (
      <>
        <Stack.Screen options={{ title: "Passkey added" }} />
        <Done
          title="Passkey added"
          detail="Next time you log in, use your passkey after your password. Logging in this way does not limit your withdrawals."
          action="Done"
          onPress={leave}
        />
      </>
    );
  }

  return (
    <Screen
      footer={
        <>
          <Button label="Add a passkey" onPress={add} loading={busy} testID="add-passkey" />
          <TextLink label="Not now" onPress={leave} />
        </>
      }
    >
      <Stack.Screen options={{ title: "Add a passkey" }} />
      <BackButton fallback="/security" />
      <View style={styles.badge}>
        <Icon name="key" size={36} color={colors.lime} />
      </View>
      <View style={{ marginTop: 20, gap: 10 }}>
        <Title>Add a passkey</Title>
        <Txt color={colors.muted}>
          A passkey is a key kept safely on this device. You approve it with your fingerprint, face
          or screen lock.
        </Txt>
      </View>

      {error ? (
        <View style={{ marginTop: 16 }}>
          <Notice tone="danger">{error}</Notice>
        </View>
      ) : null}

      <View style={{ marginTop: 20 }}>
        <Card padded={false}>
          {reasons.map(([title, detail], index) => (
            <View key={title} style={[styles.reason, index > 0 && styles.divider]}>
              <Txt weight="bold">{title}</Txt>
              <Txt size={fontSizes.sm} color={colors.muted} lineHeight={18}>
                {detail}
              </Txt>
            </View>
          ))}
        </Card>
      </View>

      <View style={styles.note}>
        <View style={styles.noteIcon}>
          <Icon name="phone" size={18} color={colors.black} />
        </View>
        <Txt size={fontSizes.sm} lineHeight={19} style={{ flex: 1 }}>
          <Txt size={fontSizes.sm} weight="bold" lineHeight={19}>
            No passkey?
          </Txt>{" "}
          You can still confirm a log in with an authenticator app or a code by SMS.
        </Txt>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  badge: {
    marginTop: 26,
    width: 72,
    height: 72,
    borderRadius: 24,
    backgroundColor: colors.black,
    alignItems: "center",
    justifyContent: "center",
  },
  reason: { paddingVertical: 11, marginHorizontal: 16 },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  note: {
    marginTop: 14,
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 18,
    paddingVertical: 14,
    paddingHorizontal: 16,
  },
  noteIcon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
});
