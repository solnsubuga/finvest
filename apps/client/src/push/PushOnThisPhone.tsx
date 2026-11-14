import { useEffect, useState } from "react";
import { Linking, StyleSheet, View } from "react-native";

import { useAuth } from "@/auth/AuthProvider";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors, fontSizes, radii } from "@/theme";

import { pushState, PushState, registerPush } from "./registration";

const DETAIL: Record<Exclude<PushState["status"], "unsupported">, string> = {
  on: "Alerts you choose below for push come to this phone.",
  off: "Turn it on to get the alerts you choose below for push on this phone.",
  blocked: "Notifications for Finvest are off in this phone's settings. Turn them on there, then come back.",
  failed: "Push is not on yet.",
};

/**
 * Notification settings: push on this phone. Shown only in the phone app, and
 * the phone's own prompt is shown only when the client presses "Turn on".
 */
export function PushOnThisPhone() {
  const { demo } = useAuth();
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    void pushState().then((next) => {
      if (live) setState(next);
    });
    return () => {
      live = false;
    };
  }, []);

  if (demo || !state || state.status === "unsupported") {
    return null;
  }

  const turnOn = async () => {
    setBusy(true);
    setState(await registerPush({ ask: true }));
    setBusy(false);
  };

  const on = state.status === "on";
  return (
    <Card>
      <View style={{ gap: 12 }} testID="push-on-this-phone">
        <View style={styles.top}>
          <View style={[styles.icon, on && { backgroundColor: colors.black }]}>
            <Icon name="bell" size={18} color={on ? colors.lime : colors.black} />
          </View>
          <View style={{ flex: 1 }}>
            <Txt weight="bold">Push on this phone</Txt>
            <Txt size={fontSizes.sm} color={colors.muted}>
              {DETAIL[state.status]}
            </Txt>
          </View>
          {on ? (
            <View style={styles.badge}>
              <Txt size={12} weight="bold">
                On
              </Txt>
            </View>
          ) : null}
        </View>
        {state.status === "off" || state.status === "failed" ? (
          <Button label="Turn on" height={48} onPress={turnOn} loading={busy} testID="push-turn-on" />
        ) : null}
        {state.status === "blocked" ? (
          <Button
            label="Open phone settings"
            variant="secondary"
            height={48}
            onPress={() => void Linking.openSettings()}
            testID="push-open-settings"
          />
        ) : null}
        {state.status === "failed" ? (
          <Notice tone="danger">We could not switch push on just now. Check your connection and try again.</Notice>
        ) : null}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  top: { flexDirection: "row", alignItems: "center", gap: 12 },
  icon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
  badge: { backgroundColor: colors.lime, borderRadius: radii.pill, paddingVertical: 4, paddingHorizontal: 12 },
});
