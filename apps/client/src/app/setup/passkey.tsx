import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { stepOf } from "@/auth/signupSteps";
import { describeAuthError } from "@/auth/types";
import { passkeysSupported } from "@/auth/webauthn";
import { Card, Notice, StepProgress } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Done } from "@/components/Done";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { needsSignInAgain } from "@/components/SignInAgain";
import { Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

const reasons = [
  ["It is your second step", "After your password, the passkey proves it is really you."],
  [
    "It cannot be stolen by a fake site",
    "There is no code to read out, so nobody can trick you into sharing it.",
  ],
  [
    "No SMS to wait for",
    "You approve on this device, so there is no code to wait for. You still need a connection.",
  ],
] as const;

/**
 * The offer of a passkey at the end of sign up, from the design canvas. "Not
 * now" keeps SMS codes as the second step; a passkey can be added later from
 * Sign in and security.
 */
export default function OfferPasskey() {
  const router = useRouter();
  const { client, pinApplies, setJustSignedUp } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState(false);

  if (!passkeysSupported()) {
    return <Redirect href="/" />;
  }

  const finish = () => {
    setJustSignedUp(false);
    router.replace("/");
  };

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await client.addPasskey();
      setAdded(true);
    } catch (caught) {
      // Sign up has only just opened the session, so it is never too old; if it were, the
      // passkey waits for Sign in and security rather than a log in now.
      setError(
        needsSignInAgain(caught)
          ? "This took a while, so add your passkey from Profile, Sign in and security instead."
          : describeAuthError(caught),
      );
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
          action="Continue"
          onPress={finish}
        />
      </>
    );
  }

  return (
    <Screen
      signedIn
      footer={
        <>
          <Button label="Add a passkey" onPress={add} loading={busy} testID="offer-add-passkey" />
          <TextLink label="Not now, text me codes instead" onPress={finish} testID="offer-not-now" />
        </>
      }
    >
      <Stack.Screen options={{ title: "Add a passkey" }} />
      <StepProgress {...stepOf("passkey", pinApplies)} />
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
          We send a code by SMS as your second step instead. You can add a passkey any time in
          Profile, Sign in and security.
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
