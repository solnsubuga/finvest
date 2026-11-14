import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { TextField } from "@/components/Field";
import { Icon, IconName } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { useProfile } from "@/profile/ProfileProvider";
import { describeProfileError, ProfileError } from "@/profile/types";
import { colors, fontSizes, radii } from "@/theme";

const WHAT_HAPPENS: { icon: IconName; title: string; detail: string }[] = [
  { icon: "phone", title: "Everyone is signed out", detail: "On every phone and browser, this one too." },
  { icon: "lock", title: "Nobody can log in", detail: "Not even you, until your account is unfrozen." },
  {
    icon: "portfolio",
    title: "Your money carries on",
    detail: "Investments and automatic plans carry on as before. Nobody can withdraw or change anything.",
  },
  {
    icon: "shield",
    title: "Two people unfreeze it",
    detail: "Get in touch with Finvest. Two of our staff check it is really you first.",
  },
];

/**
 * Profile, Freeze my account: for a lost phone or a password someone else may know. Freezing
 * takes effect at once and cannot be undone from the app (docs/client-profile.md).
 */
export default function FreezeAccount() {
  const profile = useProfile();
  const router = useRouter();
  const { setSession, setFrozen } = useAuth();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const freeze = async () => {
    setBusy(true);
    setNote(null);
    try {
      await profile.freezeAccount(reason.trim() || null);
      // The server has ended every session. The frozen screen stays up with none.
      setFrozen(true);
      setSession(null);
    } catch (caught) {
      setNote(
        caught instanceof ProfileError && caught.code === "conflict"
          ? "Your account is already frozen or closed."
          : describeProfileError(caught),
      );
      setBusy(false);
    }
  };

  return (
    <Screen
      signedIn
      footer={
        <View style={{ gap: 6 }}>
          <Button label="Freeze my account" icon="lock" loading={busy} onPress={freeze} testID="freeze-confirm" />
          <TextLink label="Not now" onPress={() => router.back()} testID="freeze-cancel" />
        </View>
      }
    >
      <Stack.Screen options={{ title: "Freeze my account" }} />
      <BackButton fallback="/profile" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Profile</Eyebrow>
        <Title>Freeze your account</Title>
        <Txt color={colors.muted}>
          Lost your phone, or think someone else knows your password? Freeze your account and it is
          locked straight away.
        </Txt>
      </View>

      <View style={{ marginTop: 22, gap: 14 }}>
        {note ? <Notice tone="danger">{note}</Notice> : null}
        <Card padded={false}>
          {WHAT_HAPPENS.map((item, index) => (
            <View key={item.title} style={[styles.row, index > 0 && styles.divider]}>
              <View style={styles.tile}>
                <Icon name={item.icon} size={18} />
              </View>
              <View style={{ flex: 1 }}>
                <Txt weight="bold">{item.title}</Txt>
                <Txt size={fontSizes.sm} color={colors.muted}>
                  {item.detail}
                </Txt>
              </View>
            </View>
          ))}
        </Card>
        <TextField
          label="What happened? (optional)"
          value={reason}
          onChangeText={setReason}
          maxLength={500}
          testID="freeze-reason"
        />
        <View style={styles.info}>
          <Txt size={fontSizes.sm}>
            You cannot unfreeze your account from the app. This keeps it safe if someone else has your
            phone.
          </Txt>
        </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingVertical: 14, paddingHorizontal: 16 },
  divider: { borderTopWidth: 1, borderTopColor: colors.border },
  tile: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
  info: { backgroundColor: colors.surfaceSunken, borderRadius: radii.md, paddingVertical: 12, paddingHorizontal: 14 },
});
