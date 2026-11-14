import { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { biometricOnThisPhone, forgetUnlockKey, keepUnlockKey, unlockKeyKept } from "@/auth/biometric";
import { PIN_LENGTH } from "@/auth/pin";
import { AuthError, describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Done } from "@/components/Done";
import { Icon } from "@/components/Icon";
import { PinDots, PinPad } from "@/components/pin/PinPad";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

type Stage = "loading" | "off" | "pin" | "on" | "turned-on" | "turned-off" | "none";

/**
 * Sign in and security: unlock with fingerprint or face, on this phone only. Turning it on asks
 * for the PIN once; the server then gives this session its own unlock key, which the phone keeps
 * behind the fingerprint or face. The PIN is never kept on the phone, and withdrawals still ask
 * for it.
 */
export default function FingerprintUnlock() {
  const router = useRouter();
  const { client, session, pinApplies, setSession, setLocked, setSignedOutForPins } = useAuth();
  const [stage, setStage] = useState<Stage>("loading");
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void (async () => {
      const can = await biometricOnThisPhone();
      const on = can && (await unlockKeyKept());
      if (live) setStage(!can ? "none" : on ? "on" : "off");
    })();
    return () => {
      live = false;
    };
  }, []);

  if (!session || !pinApplies || !session.pinSet) {
    return <Redirect href="/security" />;
  }

  const leave = () => router.dismissTo("/security");

  const turnOn = async (tried: string) => {
    setBusy(true);
    try {
      const answer = await client.turnOnUnlockKey(tried);
      if (!answer.enabled || !answer.key) {
        setPin("");
        setError(
          answer.triesLeft === 1
            ? "That PIN is not right. 1 try left before this phone is signed out."
            : `That PIN is not right. ${answer.triesLeft} tries left before this phone is signed out.`,
        );
        return;
      }
      try {
        await keepUnlockKey(answer.key);
      } catch {
        // The phone did not keep it, for instance because the prompt was cancelled.
        await client.turnOffUnlockKey().catch(() => {});
        setPin("");
        setError("Your phone did not confirm your fingerprint or face, so unlock is still off.");
        setStage("off");
        return;
      }
      setStage("turned-on");
    } catch (caught) {
      setPin("");
      if (caught instanceof AuthError && caught.code === "signed_out") {
        await forgetUnlockKey();
        setSignedOutForPins(true);
        setLocked(false);
        setSession(null);
      } else {
        setError(describeAuthError(caught));
      }
    } finally {
      setBusy(false);
    }
  };

  const turnOff = async () => {
    setBusy(true);
    setError(null);
    try {
      await client.turnOffUnlockKey();
      await forgetUnlockKey();
      setStage("turned-off");
    } catch (caught) {
      setError(describeAuthError(caught));
    } finally {
      setBusy(false);
    }
  };

  if (stage === "loading") {
    return (
      <Screen>
        <View style={{ marginTop: 80 }}>
          <ActivityIndicator accessibilityLabel="Loading" color={colors.black} />
        </View>
      </Screen>
    );
  }

  if (stage === "turned-on") {
    return (
      <>
        <Stack.Screen options={{ title: "Fingerprint or face unlock is on" }} />
        <Done
          title="Fingerprint or face unlock is on"
          detail="Next time the app locks, open it with your fingerprint or face. Your PIN still works, and withdrawals still ask for it."
          action="Done"
          onPress={leave}
        />
      </>
    );
  }

  if (stage === "turned-off") {
    return (
      <>
        <Stack.Screen options={{ title: "Fingerprint or face unlock is off" }} />
        <Done
          title="Fingerprint or face unlock is off"
          detail="The app opens with your PIN on this phone. You can turn it on again here at any time."
          action="Done"
          onPress={leave}
        />
      </>
    );
  }

  if (stage === "pin") {
    return (
      <Screen
        dark
        signedIn
        footer={<TextLink label="Cancel" onDark onPress={() => setStage("off")} testID="fingerprint-cancel" />}
      >
        <Stack.Screen options={{ title: "Confirm with your PIN" }} />
        <View style={styles.center}>
          <View style={styles.badge}>
            <Icon name="fingerprint" size={30} color={colors.black} />
          </View>
          <Title onDark center>
            Confirm with your PIN
          </Title>
          <Txt color={colors.onBlackMuted} center>
            Enter your PIN to turn on fingerprint or face unlock
          </Txt>
          <View style={{ marginTop: 14 }}>
            <PinDots count={pin.length} invalid={Boolean(error)} />
          </View>
          <View style={{ minHeight: 44, justifyContent: "center" }} accessibilityLiveRegion="polite">
            {error ? (
              <Txt size={fontSizes.sm} weight="semibold" color={colors.lime} center testID="fingerprint-error">
                {error}
              </Txt>
            ) : null}
          </View>
        </View>
        <PinPad
          value={pin}
          onChange={(next) => {
            setPin(next);
            setError(null);
            if (next.length === PIN_LENGTH) void turnOn(next);
          }}
          disabled={busy}
        />
      </Screen>
    );
  }

  const on = stage === "on";
  return (
    <Screen
      footer={
        stage === "none" ? (
          <Button label="Back" onPress={leave} testID="fingerprint-back" />
        ) : on ? (
          <>
            <Button label="Turn off" onPress={turnOff} loading={busy} testID="fingerprint-off" />
            <TextLink label="Keep it on" onPress={leave} />
          </>
        ) : (
          <Button
            label="Turn on"
            onPress={() => {
              setError(null);
              setPin("");
              setStage("pin");
            }}
            testID="fingerprint-on"
          />
        )
      }
    >
      <Stack.Screen options={{ title: "Unlock with fingerprint or face" }} />
      <BackButton fallback="/security" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Sign in and security</Eyebrow>
        <Title>Unlock with fingerprint or face</Title>
        <Txt color={colors.muted}>
          {stage === "none"
            ? "Set up a fingerprint or face in your phone's settings first. Until then, the app opens with your PIN."
            : on
              ? "It is on for this phone. When the app locks, it asks for your fingerprint or face, and your PIN still works."
              : "Open the app on this phone with the fingerprint or face you already use to unlock it, instead of typing your PIN."}
        </Txt>
      </View>
      <View style={{ marginTop: 22, gap: 12 }}>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        {stage === "none" ? null : (
          <View style={styles.points}>
            {[
              "Your PIN is never kept on this phone. Finvest gives this phone its own unlock key instead.",
              "Withdrawals and new payout accounts still ask for your PIN.",
              "It goes when you log out, choose a new PIN or someone adds a new fingerprint to the phone.",
            ].map((point) => (
              <View key={point} style={styles.point}>
                <Icon name="check" size={16} strokeWidth={2.4} />
                <Txt size={fontSizes.sm} style={{ flex: 1 }}>
                  {point}
                </Txt>
              </View>
            ))}
          </View>
        )}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  center: { marginTop: 24, alignItems: "center", gap: 12 },
  badge: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 4,
  },
  points: { gap: 12 },
  point: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
});
