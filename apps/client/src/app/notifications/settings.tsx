import { useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { Stack } from "expo-router";

import { Card, Notice } from "@/components/Blocks";
import { Icon } from "@/components/Icon";
import { LoadFailed, Loading } from "@/components/Loading";
import { Screen } from "@/components/Screen";
import { StackHeader } from "@/components/StackHeader";
import { Txt } from "@/components/Txt";
import { categories, channelNames, channels } from "@/support/labels";
import { useSupport } from "@/support/SupportProvider";
import { Category, CategoryChannels, Channel, describeSupportError } from "@/support/types";
import { colors, fontSizes } from "@/theme";

type Loaded =
  | { status: "loading" }
  | { status: "failed"; message: string }
  | { status: "ready"; preferences: CategoryChannels[] };

const KEEP_ONE = "Security alerts always go by SMS or email, so one of them stays on.";

/** Which alerts reach the client by push, SMS and email. Each change is saved as it is made. */
export default function NotificationSettings() {
  const { client } = useSupport();
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [saving, setSaving] = useState<string | null>(null);
  const [note, setNote] = useState<{ category: Category; tone: "neutral" | "danger"; text: string } | null>(
    null,
  );
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    client
      .getPreferences()
      .then((preferences) => {
        if (live) {
          setLoaded({ status: "ready", preferences });
        }
      })
      .catch((error: unknown) => {
        if (live) {
          setLoaded({ status: "failed", message: describeSupportError(error) });
        }
      });
    return () => {
      live = false;
    };
  }, [client, attempt]);

  const toggle = async (row: CategoryChannels, channel: Channel) => {
    if (saving) {
      return;
    }
    const next = row.channels.includes(channel)
      ? row.channels.filter((candidate) => candidate !== channel)
      : [...row.channels, channel];
    if (row.alwaysOn && !next.includes("sms") && !next.includes("email")) {
      setNote({ category: row.category, tone: "neutral", text: KEEP_ONE });
      return;
    }
    setNote(null);
    setSaving(`${row.category}-${channel}`);
    try {
      const preferences = await client.savePreferences(row.category, next);
      setLoaded({ status: "ready", preferences });
    } catch (error) {
      // Nothing changed on screen, so the switch still shows what is saved.
      setNote({ category: row.category, tone: "danger", text: describeSupportError(error) });
    } finally {
      setSaving(null);
    }
  };

  return (
    <Screen signedIn>
      <Stack.Screen options={{ title: "Notification settings" }} />
      <StackHeader title="Notification settings" fallback="/notifications" />
      <View style={{ marginTop: 20, gap: 12 }}>
        <Txt size={14} color={colors.muted}>
          Every alert is kept in the app. Choose which ones also reach you by push, SMS or email.
        </Txt>
        {loaded.status === "loading" ? <Loading label="Loading your settings" /> : null}
        {loaded.status === "failed" ? (
          <LoadFailed
            message={loaded.message}
            onRetry={() => {
              setLoaded({ status: "loading" });
              setAttempt((value) => value + 1);
            }}
          />
        ) : null}
        {loaded.status === "ready"
          ? categories.map(({ category, title, about }) => {
              const row = loaded.preferences.find((candidate) => candidate.category === category);
              if (!row) {
                return null;
              }
              return (
                <Card key={category}>
                  <View style={{ gap: 12 }}>
                    <View style={{ gap: 2 }}>
                      <Txt weight="bold">{title}</Txt>
                      <Txt size={fontSizes.sm} color={colors.muted}>
                        {about}
                      </Txt>
                    </View>
                    <View style={{ flexDirection: "row", gap: 8 }}>
                      {channels.map((channel) => {
                        const on = row.channels.includes(channel);
                        return (
                          <Pressable
                            key={channel}
                            accessibilityRole="switch"
                            accessibilityLabel={`${title} by ${channelNames[channel]}`}
                            accessibilityState={{
                              checked: on,
                              busy: saving === `${category}-${channel}`,
                            }}
                            onPress={() => void toggle(row, channel)}
                            style={({ pressed }) => [
                              styles.chip,
                              on && styles.chipOn,
                              pressed && { opacity: 0.7 },
                            ]}
                            testID={`pref-${category}-${channel}`}
                          >
                            {on ? (
                              <Icon name="check" size={14} color={colors.white} strokeWidth={3} />
                            ) : null}
                            <Txt size={13} weight="semibold" color={on ? colors.white : colors.ink}>
                              {channelNames[channel]}
                            </Txt>
                          </Pressable>
                        );
                      })}
                    </View>
                    {note && note.category === category ? (
                      <Notice tone={note.tone}>{note.text}</Notice>
                    ) : null}
                  </View>
                </Card>
              );
            })
          : null}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  chip: {
    flex: 1,
    height: 44,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
  chipOn: { backgroundColor: colors.black, borderColor: colors.black },
});
