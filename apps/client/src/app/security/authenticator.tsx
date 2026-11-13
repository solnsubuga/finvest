import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Linking, StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { AuthenticatorSetup, describeAuthError } from "@/auth/types";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import { Done } from "@/components/Done";
import { QrCode } from "@/components/QrCode";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

/** "ABCD EFGH ..." so the key can be typed without losing the place. */
export function groupKey(key: string): string {
  return key.replace(/(.{4})(?=.)/g, "$1 ");
}

type Step = "key" | "code" | "done";

/**
 * Set up an authenticator app. Step 1 hands the key to the app, by QR code,
 * by opening the app, or by typing. Step 2 asks for a code from the app, and
 * only a right code switches it on.
 *
 * The key lives in this screen's memory and nowhere else. It is not put in
 * the route, the session or storage, and it is gone when the screen closes.
 */
export default function SetUpAuthenticator() {
  const router = useRouter();
  const { client } = useAuth();
  const wide = useIsWide();
  const [setup, setSetup] = useState<AuthenticatorSetup | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [step, setStep] = useState<Step>("key");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openFailed, setOpenFailed] = useState(false);

  useEffect(() => {
    let current = true;
    client.startAuthenticatorSetup().then(
      (next) => {
        if (current) {
          setSetup(next);
        }
      },
      (caught: unknown) => {
        if (current) {
          setLoadError(describeAuthError(caught));
        }
      },
    );
    return () => {
      current = false;
    };
  }, [client, attempt]);

  const leave = useCallback(
    () => (router.canGoBack() ? router.back() : router.replace("/security")),
    [router],
  );

  if (step === "done") {
    return (
      <>
        <Stack.Screen options={{ title: "Authenticator app is on" }} />
        <Done
          title="Authenticator app is on"
          detail="Next time you log in, enter a code from your app after your password. Logging in this way does not limit your withdrawals."
          action="Done"
          onPress={leave}
        />
      </>
    );
  }

  if (!setup) {
    return (
      <Screen>
        <Stack.Screen options={{ title: "Set up an authenticator app" }} />
        <BackButton fallback="/security" />
        {loadError ? (
          <View style={{ marginTop: 28, gap: 12 }}>
            <Notice tone="danger">{loadError}</Notice>
            <Button
              label="Try again"
              height={52}
              onPress={() => {
                setLoadError(null);
                setAttempt((value) => value + 1);
              }}
            />
          </View>
        ) : (
          <View style={{ marginTop: 60 }} accessibilityLabel="Loading">
            <ActivityIndicator color={colors.black} />
          </View>
        )}
      </Screen>
    );
  }

  if (step === "code") {
    const confirm = async () => {
      setBusy(true);
      setError(null);
      try {
        await client.confirmAuthenticatorSetup(setup.setupId, code);
        setStep("done");
      } catch (caught) {
        setError(describeAuthError(caught));
        setCode("");
      } finally {
        setBusy(false);
      }
    };
    return (
      <Screen
        footer={
          <>
            <Button
              label="Turn on"
              onPress={confirm}
              loading={busy}
              disabled={code.length < CODE_LENGTH}
              testID="code-submit"
            />
            <TextLink
              label="Show the QR code again"
              onPress={() => {
                setError(null);
                setCode("");
                setStep("key");
              }}
            />
          </>
        }
      >
        <Stack.Screen options={{ title: "Enter the code from your app" }} />
        <View style={{ marginTop: 72, gap: 8 }}>
          <Eyebrow>Step 2 of 2</Eyebrow>
          <Title>Enter the code from your app</Title>
          <Txt color={colors.muted}>
            Your authenticator app now shows a 6 digit code for Finvest. Type it here to finish. The
            code changes every 30 seconds.
          </Txt>
        </View>
        <View style={{ marginTop: 24, gap: 12 }}>
          <CodeInput
            value={code}
            onChangeText={(next) => {
              setCode(next);
              setError(null);
            }}
            invalid={Boolean(error)}
          />
          {error ? <Notice tone="danger">{error}</Notice> : null}
        </View>
      </Screen>
    );
  }

  const openApp = () => {
    setOpenFailed(false);
    Linking.openURL(setup.uri).catch(() => setOpenFailed(true));
  };

  return (
    <Screen
      footer={
        <>
          <Button label="Continue" onPress={() => setStep("code")} testID="key-continue" />
          <TextLink label="Not now" onPress={leave} />
        </>
      }
    >
      <Stack.Screen options={{ title: "Set up an authenticator app" }} />
      <BackButton fallback="/security" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Step 1 of 2</Eyebrow>
        <Title>Set up an authenticator app</Title>
        <Txt color={colors.muted}>
          The app shows a new 6 digit code every 30 seconds. Google Authenticator and Microsoft
          Authenticator both work.
        </Txt>
      </View>

      <View style={{ marginTop: 20 }}>
        <Card>
          <View style={{ alignItems: "center", gap: 12 }}>
            <QrCode
              value={setup.uri}
              size={wide ? 184 : 156}
              label="QR code to scan with your authenticator app"
            />
            <Txt size={fontSizes.sm} weight="semibold" center>
              {wide
                ? "In the app, choose to add an account, then scan this code."
                : "On another screen? In the app, choose to add an account, then scan this code."}
            </Txt>
          </View>
          <View style={styles.keyBlock}>
            <Txt size={fontSizes.sm} color={colors.muted}>
              Or type this key into the app
            </Txt>
            <Txt selectable weight="bold" lineHeight={24} style={{ letterSpacing: 0.5 }} testID="setup-key">
              {groupKey(setup.key)}
            </Txt>
          </View>
        </Card>
      </View>

      {wide ? null : (
        <View style={{ marginTop: 4 }}>
          <TextLink label="Open my authenticator app on this phone" onPress={openApp} />
        </View>
      )}
      {openFailed ? (
        <Notice tone="neutral">
          No authenticator app opened. Install one first, or type the key into the app yourself.
        </Notice>
      ) : null}

      <View style={{ marginTop: wide ? 14 : 4 }}>
        <Notice tone="warning">
          Keep this key to yourself. Nobody from Finvest will ever ask for it or for a code from your
          app.
        </Notice>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  keyBlock: {
    marginTop: 16,
    paddingTop: 14,
    borderTopWidth: 1,
    borderTopColor: colors.divider,
    gap: 2,
  },
});
