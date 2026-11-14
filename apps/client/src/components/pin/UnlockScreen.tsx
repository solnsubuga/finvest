import { useCallback, useEffect, useRef, useState } from "react";
import { StyleSheet, View } from "react-native";
import { router } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { biometricOnThisPhone, forgetUnlockKey, readUnlockKey, unlockKeyKept } from "@/auth/biometric";
import { PIN_LENGTH } from "@/auth/pin";
import { AuthError, describeAuthError } from "@/auth/types";
import { colors, fontSizes } from "@/theme";

import { Notice } from "../Blocks";
import { Button, TextLink } from "../Button";
import { Icon } from "../Icon";
import { LogoMark } from "../Logo";
import { Screen } from "../Screen";
import { Title, Txt } from "../Txt";
import { PinDots, PinPad } from "./PinPad";

type Stage = "pin" | "forgot";

/**
 * Unlock with PIN, from the design canvas. Shown in place of the whole app
 * while it is locked, so nothing behind it is reachable. "Forgot your PIN?"
 * leads to Reset your PIN, which is logging in again.
 */
export function UnlockScreen() {
  const { client, session, setSession, setLocked, setSignedOutForPins } = useAuth();
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState<Stage>("pin");
  /** True when this phone keeps an unlock key behind its fingerprint or face. */
  const [biometric, setBiometric] = useState(false);
  const asked = useRef(false);

  const firstName = session?.displayName?.split(/\s+/)[0];

  const check = async (tried: string) => {
    setBusy(true);
    try {
      const checked = await client.unlockWithPin(tried);
      if (checked.unlocked) {
        setLocked(false);
        return;
      }
      setPin("");
      setError(
        checked.triesLeft === 1
          ? "That PIN is not right. 1 try left before this phone is signed out."
          : `That PIN is not right. ${checked.triesLeft} tries left before this phone is signed out.`,
      );
    } catch (caught) {
      setPin("");
      if (caught instanceof AuthError && caught.code === "signed_out") {
        await forgetUnlockKey();
        signedOut();
      } else {
        setError(describeAuthError(caught));
      }
    } finally {
      setBusy(false);
    }
  };

  const signedOut = useCallback(() => {
    setSignedOutForPins(true);
    setLocked(false);
    setSession(null);
  }, [setLocked, setSession, setSignedOutForPins]);

  /** Asks for the fingerprint or face. Cancelling it leaves the PIN pad, as before. */
  const openWithBiometric = useCallback(async () => {
    const key = await readUnlockKey();
    if (key === null) {
      setBiometric(await unlockKeyKept());
      return;
    }
    setBusy(true);
    try {
      const checked = await client.unlockWithKey(key);
      if (checked.unlocked) {
        setLocked(false);
        return;
      }
      // The server no longer knows the key, for instance after a new PIN, and has turned it off.
      await forgetUnlockKey();
      setBiometric(false);
      setError("Fingerprint or face unlock is off for this phone. Enter your PIN.");
    } catch (caught) {
      if (caught instanceof AuthError && caught.code === "signed_out") {
        await forgetUnlockKey();
        signedOut();
      } else {
        setError(describeAuthError(caught));
      }
    } finally {
      setBusy(false);
    }
  }, [client, setLocked, signedOut]);

  // Offers the fingerprint or face once, as the screen opens.
  useEffect(() => {
    let live = true;
    void (async () => {
      const ready = (await biometricOnThisPhone()) && (await unlockKeyKept());
      if (!live) return;
      setBiometric(ready);
      if (ready && !asked.current) {
        asked.current = true;
        void openWithBiometric();
      }
    })();
    return () => {
      live = false;
    };
  }, [openWithBiometric]);

  /** Logging in again is how a PIN is reset. The app then asks for a new one. */
  const logInAgain = async () => {
    setBusy(true);
    try {
      await forgetUnlockKey();
      await client.signOut();
    } finally {
      setLocked(false);
      setSession(null);
      router.replace("/login");
    }
  };

  if (stage === "forgot") {
    return (
      <Screen
        footer={
          <>
            <Button
              label="Log in again"
              onPress={logInAgain}
              loading={busy}
              testID="pin-log-in-again"
            />
            <TextLink label="Back to my PIN" onPress={() => setStage("pin")} />
          </>
        }
      >
        <View style={{ marginTop: 28, gap: 10 }}>
          <Title>Reset your PIN</Title>
          <Txt color={colors.muted}>
            Your PIN only works on this phone. To choose a new one, log in again
            with your password and your passkey, authenticator app or a code.
            Your money stays exactly where it is.
          </Txt>
        </View>
        <View style={styles.steps}>
          {[
            [
              "1",
              "Log in again",
              "With your mobile number or email, and your password.",
            ],
            [
              "2",
              "Confirm it is you",
              "With your passkey, your authenticator app or a code.",
            ],
            ["3", "Choose a new PIN", "Five digits, for this phone only."],
          ].map(([number, title, detail]) => (
            <View key={number} style={styles.step}>
              <View style={styles.stepNumber}>
                <Txt size={13} weight="bold" color={colors.white}>
                  {number}
                </Txt>
              </View>
              <View style={{ flex: 1 }}>
                <Txt weight="bold">{title}</Txt>
                <Txt size={fontSizes.sm} color={colors.muted}>
                  {detail}
                </Txt>
              </View>
            </View>
          ))}
        </View>
        <View style={{ marginTop: 14 }}>
          <Notice tone="warning">
            Logging in with a code instead of a passkey or an authenticator app
            limits withdrawals to accounts you have used before for 24 hours.
          </Notice>
        </View>
      </Screen>
    );
  }

  return (
    <Screen
      dark
      signedIn
      footer={
        <>
          {biometric ? (
            <Button
              label="Use fingerprint or face"
              variant="lime"
              onPress={() => void openWithBiometric()}
              disabled={busy}
              testID="unlock-biometric"
            />
          ) : null}
          <TextLink
            label="Forgot your PIN?"
            onDark
            onPress={() => setStage("forgot")}
            testID="pin-forgot"
          />
        </>
      }
    >
      <View style={styles.center}>
        <LogoMark size={56} onDark />
        <Title onDark>
          {firstName ? `Welcome back, ${firstName}` : "Welcome back"}
        </Title>
        <Txt color={colors.onBlackMuted} center>
          Enter your PIN to open the app on this phone
        </Txt>
        <View style={{ marginTop: 14 }}>
          <PinDots count={pin.length} invalid={Boolean(error)} />
        </View>
        <View
          style={{ minHeight: 44, justifyContent: "center" }}
          accessibilityLiveRegion="polite"
        >
          {error ? (
            <Txt
              size={fontSizes.sm}
              weight="semibold"
              color={colors.lime}
              center
            >
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
          if (next.length === PIN_LENGTH) void check(next);
        }}
        disabled={busy}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  center: { marginTop: 24, alignItems: "center", gap: 12 },
  lockBadge: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 8,
  },
  steps: { marginTop: 22, gap: 14 },
  step: { flexDirection: "row", gap: 12, alignItems: "flex-start" },
  stepNumber: {
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: colors.black,
    alignItems: "center",
    justifyContent: "center",
  },
});

/**
 * After the fifth wrong PIN. The server has ended the session, so this shows with no session
 * at all and stays until the client chooses to log in again.
 */
export function SignedOutForPins() {
  const { setSignedOutForPins } = useAuth();
  const logIn = () => {
    setSignedOutForPins(false);
    router.replace("/login");
  };
  return (
    <Screen
      dark
      footer={<Button label="Log in" variant="lime" onPress={logIn} testID="pin-log-in-again" />}
    >
      <View style={styles.center}>
        <View style={styles.lockBadge}>
          <Icon name="shield" size={34} color={colors.black} />
        </View>
        <Title onDark center>
          Signed out for your safety
        </Title>
        <Txt color={colors.onBlackMuted} center>
          {describeAuthError(new AuthError("signed_out"))}
        </Txt>
      </View>
    </Screen>
  );
}
