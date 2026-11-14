import { useCallback, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { Stack, useFocusEffect, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { describeAuthError, Passkey } from "@/auth/types";
import { useSecondSteps } from "@/auth/useSecondSteps";
import { accountWhen } from "@/auth/when";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { needsSignInAgain, SignInAgain } from "@/components/SignInAgain";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors, fontSizes } from "@/theme";

type State =
  | { status: "loading" }
  | { status: "ready"; passkeys: Passkey[]; at: number }
  | { status: "error"; message: string };

/** Sign in and security, Passkeys: the ones saved to this account, and removing one. */
export default function Passkeys() {
  const router = useRouter();
  const { client } = useAuth();
  const steps = useSecondSteps();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const [note, setNote] = useState<{ tone: "neutral" | "danger"; text: string } | null>(null);

  useFocusEffect(
    useCallback(() => {
      let current = true;
      client.listPasskeys().then(
        (passkeys) => current && setState({ status: "ready", passkeys, at: Date.now() }),
        (caught: unknown) => current && setState({ status: "error", message: describeAuthError(caught) }),
      );
      return () => {
        current = false;
      };
      // `attempt` is here so "Try again" and a removal ask once more.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [client, attempt]),
  );

  const reload = () => setAttempt((value) => value + 1);

  const remove = async (passkey: Passkey) => {
    setBusy(true);
    setNote(null);
    try {
      await client.removePasskey(passkey.id);
      setNote({ tone: "neutral", text: `The passkey "${passkey.name}" is removed. It can no longer be used to log in.` });
      setConfirming(null);
      reload();
    } catch (caught) {
      if (needsSignInAgain(caught)) {
        setStale(true);
      } else {
        setNote({ tone: "danger", text: describeAuthError(caught) });
        setConfirming(null);
        reload();
      }
    } finally {
      setBusy(false);
    }
  };

  if (stale) {
    return (
      <Screen>
        <Stack.Screen options={{ title: "Passkeys" }} />
        <BackButton fallback="/security" />
        <SignInAgain returnTo="/security/passkeys" />
      </Screen>
    );
  }

  // When the list was read, so "today" and "yesterday" stay put while the screen is open.
  const now = state.status === "ready" ? state.at : 0;

  return (
    <Screen
      footer={
        <Button
          label="Add a passkey on this device"
          variant="secondary"
          onPress={() => router.push("/security/passkey")}
          testID="add-passkey"
        />
      }
    >
      <Stack.Screen options={{ title: "Passkeys" }} />
      <BackButton fallback="/security" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Sign in and security</Eyebrow>
        <Title>Passkeys</Title>
        <Txt color={colors.muted}>
          Each passkey lives on one phone, computer or password manager. Remove one you no longer
          use, or one on a device you have lost.
        </Txt>
      </View>

      {state.status === "loading" ? (
        <View style={{ marginTop: 40 }} accessibilityLabel="Loading">
          <ActivityIndicator color={colors.black} />
        </View>
      ) : null}

      {state.status === "error" ? (
        <View style={{ marginTop: 22, gap: 12 }}>
          <Notice tone="danger">{state.message}</Notice>
          <Button label="Try again" onPress={reload} height={52} />
        </View>
      ) : null}

      {state.status === "ready" ? (
        <View style={{ marginTop: 22, gap: 14 }}>
          {note ? <Notice tone={note.tone}>{note.text}</Notice> : null}
          {state.passkeys.length === 0 ? (
            <Card>
              <Txt color={colors.muted} testID="no-passkeys">
                You have no passkeys. Add one to log in with your fingerprint, face or screen lock.
              </Txt>
            </Card>
          ) : (
            <Card padded={false}>
              {state.passkeys.map((passkey, index) => (
                <View key={passkey.id} style={[styles.item, index > 0 && styles.divider]}>
                  <View style={styles.row} testID={`passkey-${passkey.id}`}>
                    <View style={styles.icon}>
                      <Icon name="key" size={18} color={colors.black} />
                    </View>
                    <View style={{ flex: 1, gap: 2 }}>
                      <Txt weight="bold">{passkey.name}</Txt>
                      <Txt size={fontSizes.sm} color={colors.muted}>
                        {`Added ${accountWhen(passkey.createdAt, now)}. ${
                          passkey.lastUsedAt
                            ? `Last used ${accountWhen(passkey.lastUsedAt, now)}.`
                            : "Not used to log in yet."
                        }`}
                      </Txt>
                    </View>
                    {confirming === passkey.id ? null : (
                      <TextLink
                        label="Remove"
                        onPress={() => {
                          setNote(null);
                          setConfirming(passkey.id);
                        }}
                      />
                    )}
                  </View>
                  {confirming === passkey.id ? (
                    <View style={styles.confirm}>
                      <Txt size={fontSizes.sm}>
                        {state.passkeys.length === 1
                          ? steps.status === "ready" && steps.methods.includes("authenticator")
                            ? "This is your only passkey. After removing it you log in with your authenticator app or an SMS code."
                            : "This is your only passkey. After removing it you log in with an SMS code, and every log in limits withdrawals to accounts you have used before for 24 hours."
                          : "You will no longer be able to log in with this passkey."}
                      </Txt>
                      <Button
                        label="Remove passkey"
                        onPress={() => remove(passkey)}
                        loading={busy}
                        height={48}
                        testID="confirm-remove-passkey"
                      />
                      <TextLink label="Keep it" onPress={() => setConfirming(null)} />
                    </View>
                  ) : null}
                </View>
              ))}
            </Card>
          )}
        </View>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  item: { paddingHorizontal: 16, paddingVertical: 4 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12, minHeight: 60 },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  confirm: { gap: 10, paddingBottom: 14 },
  icon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
});
