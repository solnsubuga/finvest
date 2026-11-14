import { ReactNode, useCallback, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { Stack, useFocusEffect } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { describeAuthError, Device } from "@/auth/types";
import { accountWhen } from "@/auth/when";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { colors, fontSizes, radii } from "@/theme";

type State =
  | { status: "loading" }
  | { status: "ready"; devices: Device[]; at: number }
  | { status: "error"; message: string };

const CONFIRMED: Record<Device["confirmedWith"], string> = {
  passkey: "a passkey",
  authenticator: "the authenticator app",
  code: "an SMS code",
  signup: "sign up",
};

/** Sign in and security, Devices: where this account is signed in, and signing the others out. */
export default function Devices() {
  const { client } = useAuth();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const [note, setNote] = useState<{ tone: "neutral" | "danger"; text: string } | null>(null);

  useFocusEffect(
    useCallback(() => {
      let current = true;
      client.listDevices().then(
        (devices) => current && setState({ status: "ready", devices, at: Date.now() }),
        (caught: unknown) => current && setState({ status: "error", message: describeAuthError(caught) }),
      );
      return () => {
        current = false;
      };
      // `attempt` is here so "Try again" and a sign out ask once more.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [client, attempt]),
  );

  const reload = () => setAttempt((value) => value + 1);

  const signOutOne = async (device: Device) => {
    setBusy(device.id);
    setNote(null);
    try {
      await client.signOutDevice(device.id);
      setNote({ tone: "neutral", text: `${device.label} is signed out.` });
    } catch (caught) {
      setNote({ tone: "danger", text: describeAuthError(caught) });
    } finally {
      setBusy(null);
      reload();
    }
  };

  const signOutOthers = async () => {
    setBusy("others");
    setNote(null);
    try {
      const ended = await client.signOutOtherDevices();
      setNote({
        tone: "neutral",
        text:
          ended === 1
            ? "1 other device is signed out."
            : `${ended} other devices are signed out.`,
      });
    } catch (caught) {
      setNote({ tone: "danger", text: describeAuthError(caught) });
    } finally {
      setBusy(null);
      setConfirmAll(false);
      reload();
    }
  };

  // When the list was read, so "today" and "yesterday" stay put while the screen is open.
  const now = state.status === "ready" ? state.at : 0;
  const others = state.status === "ready" ? state.devices.filter((d) => !d.current) : [];
  const here = state.status === "ready" ? state.devices.find((d) => d.current) : undefined;

  return (
    <Screen>
      <Stack.Screen options={{ title: "Devices" }} />
      <BackButton fallback="/security" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Sign in and security</Eyebrow>
        <Title>Devices</Title>
        <Txt color={colors.muted}>
          Everywhere your account is signed in. If you do not know a device, sign it out. If someone
          may know your password, log out and use &quot;Forgot password?&quot; to set a new one.
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
          {here ? (
            <Card padded={false}>
              <DeviceRow device={here} now={now} />
            </Card>
          ) : null}

          <Eyebrow>Other devices</Eyebrow>
          {others.length === 0 ? (
            <Card>
              <Txt color={colors.muted} testID="no-other-devices">
                Your account is not signed in anywhere else.
              </Txt>
            </Card>
          ) : (
            <>
              <Card padded={false}>
                {others.map((device, index) => (
                  <View key={device.id} style={index > 0 && styles.divider}>
                    <DeviceRow
                      device={device}
                      now={now}
                      action={
                        busy === device.id ? (
                          <ActivityIndicator color={colors.black} />
                        ) : (
                          <TextLink
                            label="Sign out"
                            onPress={() => (busy === null ? signOutOne(device) : undefined)}
                            testID={`sign-out-${device.id}`}
                          />
                        )
                      }
                    />
                  </View>
                ))}
              </Card>
              {confirmAll ? (
                <Card>
                  <View style={{ gap: 12 }}>
                    <Txt weight="bold">Sign out every other device?</Txt>
                    <Txt size={fontSizes.sm} color={colors.muted}>
                      They will need your password and a second step to get back in. This device
                      stays signed in.
                    </Txt>
                    <Button
                      label="Sign out all others"
                      onPress={signOutOthers}
                      loading={busy === "others"}
                      height={52}
                      testID="confirm-sign-out-others"
                    />
                    <TextLink label="Keep them" onPress={() => setConfirmAll(false)} />
                  </View>
                </Card>
              ) : (
                <Button
                  label="Sign out all other devices"
                  variant="secondary"
                  onPress={() => setConfirmAll(true)}
                  disabled={busy !== null}
                  height={52}
                  testID="sign-out-others"
                />
              )}
            </>
          )}
        </View>
      ) : null}
    </Screen>
  );
}

function DeviceRow({ device, now, action }: { device: Device; now: number; action?: ReactNode }) {
  return (
    <View style={styles.row} testID={`device-${device.id}`}>
      <View style={[styles.icon, device.current && { backgroundColor: colors.black }]}>
        <Icon
          name={device.kind === "app" ? "phone" : "documents"}
          size={18}
          color={device.current ? colors.lime : colors.black}
        />
      </View>
      <View style={{ flex: 1, gap: 2 }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <Txt weight="bold">{device.label}</Txt>
          {device.current ? (
            <View style={styles.badge}>
              <Txt size={12} weight="bold">
                This device
              </Txt>
            </View>
          ) : null}
        </View>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {device.current
            ? `Signed in ${accountWhen(device.signedInAt, now)} with ${CONFIRMED[device.confirmedWith]}`
            : `Last used ${accountWhen(device.lastSeenAt, now)}. Signed in with ${CONFIRMED[device.confirmedWith]}.`}
        </Txt>
      </View>
      {action}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, minHeight: 68 },
  divider: { borderTopWidth: 1, borderTopColor: colors.divider },
  icon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
  badge: {
    backgroundColor: colors.lime,
    borderRadius: radii.pill,
    paddingVertical: 2,
    paddingHorizontal: 10,
  },
});
