import { useState } from "react";
import { Platform, Pressable, StyleSheet, View } from "react-native";
import { Stack, useRouter } from "expo-router";

import { BackButton } from "@/components/BackButton";
import { Card, Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Choice } from "@/components/Choice";
import { Icon } from "@/components/Icon";
import { Screen } from "@/components/Screen";
import { SignInAgain } from "@/components/SignInAgain";
import { ToggleRow } from "@/components/Toggle";
import { Eyebrow, Title, Txt } from "@/components/Txt";
import { useDataSaving } from "@/dataSaving/DataSavingProvider";
import { canTellConnection } from "@/dataSaving/network";
import { useProfile } from "@/profile/ProfileProvider";
import { describeProfileError, ProfileError } from "@/profile/types";
import { colors, fontSizes } from "@/theme";

// Only English has reviewed wording. The others are shown so clients know they are coming.
const LANGUAGES = [
  { key: "en", label: "English", detail: "Every screen and message", ready: true },
  { key: "lg", label: "Luganda", detail: "Coming soon", ready: false },
  { key: "sw", label: "Kiswahili", detail: "Coming soon", ready: false },
] as const;

/**
 * Profile, Language and data: the app's language, data saving on this device,
 * SMS receipts and a copy of my data.
 */
export default function LanguageAndData() {
  const profile = useProfile();
  const saving = useDataSaving();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "neutral" | "danger"; text: string } | null>(null);
  const [signInAgain, setSignInAgain] = useState(false);

  const download = async () => {
    setBusy(true);
    setNote(null);
    try {
      await profile.downloadMyData();
      setNote({ tone: "neutral", text: "Your copy is saved. Keep it somewhere safe: it holds your personal details." });
    } catch (caught) {
      if (caught instanceof ProfileError && caught.code === "sign_in_again") {
        setSignInAgain(true);
      } else {
        setNote({ tone: "danger", text: describeProfileError(caught) });
      }
    } finally {
      setBusy(false);
    }
  };

  if (signInAgain) {
    return (
      <Screen signedIn>
        <BackButton fallback="/profile" />
        <SignInAgain returnTo="/account/language" />
      </Screen>
    );
  }

  return (
    <Screen signedIn>
      <Stack.Screen options={{ title: "Language and data" }} />
      <BackButton fallback="/profile" />
      <View style={{ marginTop: 28, gap: 8 }}>
        <Eyebrow>Profile</Eyebrow>
        <Title>Language and data</Title>
      </View>

      <View style={{ marginTop: 22, gap: 10 }} accessibilityRole="radiogroup">
        <Txt weight="bold">App language</Txt>
        {LANGUAGES.map((language) => (
          <Choice
            key={language.key}
            label={language.label}
            detail={language.detail}
            selected={language.key === "en"}
            disabled={!language.ready}
            testID={`language-${language.key}`}
          />
        ))}
      </View>

      <View style={{ marginTop: 26, gap: 10 }}>
        <Txt weight="bold">Data saving</Txt>
        <Card padded={false}>
          <View style={{ paddingHorizontal: 16, paddingVertical: 4 }} testID="data-saving">
            <ToggleRow
              title="Light mode for slow networks"
              detail="Figures as text instead of charts, and fewer updates in the background"
              on={saving.light}
              onChange={(light) => saving.change({ light })}
              disabled={!saving.ready}
              testID="data-saving-light"
            />
            <View style={styles.divider} />
            <ToggleRow
              title="Download documents on Wi-Fi only"
              detail={
                canTellConnection()
                  ? "Statements, receipts and certificates wait for Wi-Fi unless you say otherwise"
                  : "This browser does not say whether it is on Wi-Fi, so downloads here are never held"
              }
              on={saving.wifiOnlyDocuments}
              onChange={(wifiOnlyDocuments) => saving.change({ wifiOnlyDocuments })}
              disabled={!saving.ready}
              testID="data-saving-wifi"
            />
          </View>
        </Card>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {Platform.OS === "web" ? "These apply in this browser only." : "These apply on this phone only."}
        </Txt>
      </View>

      <View style={{ marginTop: 26, gap: 10 }}>
        <Txt weight="bold">Messages</Txt>
        <Card padded={false}>
          <Pressable
            accessibilityRole="link"
            onPress={() => router.push("/notifications/settings")}
            style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
            testID="language-sms"
          >
            <View style={{ flex: 1 }}>
              <Txt weight="bold">SMS receipts</Txt>
              <Txt size={fontSizes.sm} color={colors.muted}>
                For when you have no data. Choose which messages come by SMS, email or in the app
              </Txt>
            </View>
            <Icon name="chevron" size={16} strokeWidth={2.2} />
          </Pressable>
        </Card>
      </View>

      <View style={{ marginTop: 26, gap: 10 }}>
        <Txt weight="bold">Your data</Txt>
        <Card>
          <View style={{ gap: 12 }}>
            <Txt size={fontSizes.sm} color={colors.muted}>
              A copy of everything we hold about you: your details, next of kin, investments, payments and
              messages. It is a file you can open on a computer.
            </Txt>
            {note ? <Notice tone={note.tone}>{note.text}</Notice> : null}
            <Button
              label="Download a copy of my data"
              variant="secondary"
              height={52}
              loading={busy}
              onPress={download}
              testID="language-download"
            />
          </View>
        </Card>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, minHeight: 68 },
  divider: { height: 1, backgroundColor: colors.divider },
});
