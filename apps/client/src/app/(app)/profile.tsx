import { useCallback, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164 } from "@/auth/phone";
import { describeAuthError } from "@/auth/types";
import { AppPage } from "@/components/AppPage";
import { Card, Notice } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon, IconName } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors, COLUMN_WIDTH, fontSizes, radii } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

export default function Profile() {
  const router = useRouter();
  const { client, session, setSession } = useAuth();
  const wide = useIsWide();
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [emailNote, setEmailNote] = useState<{ tone: "neutral" | "danger"; text: string } | null>(null);

  // A link opened in another tab or on another device proves the address
  // there, so Profile asks again each time it comes into view.
  useFocusEffect(
    useCallback(() => {
      let live = true;
      client
        .restoreSession()
        .then((fresh) => {
          if (live && fresh) {
            setSession(fresh);
          }
        })
        .catch(() => {
          // Offline: keep what is shown.
        });
      return () => {
        live = false;
      };
    }, [client, setSession]),
  );

  if (!session) {
    return null;
  }

  const sendLink = async () => {
    setSending(true);
    setEmailNote(null);
    try {
      const result = await client.sendEmailProof();
      if (result === "proved") {
        setSession({ ...session, emailVerified: true });
      } else {
        setEmailNote({
          tone: "neutral",
          text: `We emailed a link to ${session.email ?? "your address"}. Open it within 24 hours, on any device. Only the newest link works.`,
        });
      }
    } catch (caught) {
      setEmailNote({ tone: "danger", text: describeAuthError(caught) });
    } finally {
      setSending(false);
    }
  };

  const signOut = async () => {
    setBusy(true);
    try {
      await client.signOut();
    } finally {
      // Leaving this device always works, even if the server cannot be told.
      setSession(null);
    }
  };

  return (
    <AppPage title="Profile">
      <View style={{ maxWidth: COLUMN_WIDTH, gap: 16 }}>
        <Card>
          <View style={{ gap: 4 }}>
            {session.displayName ? <Txt weight="bold">{session.displayName}</Txt> : null}
            <Txt size={fontSizes.sm} color={colors.muted}>
              Mobile number
            </Txt>
            <Txt weight="semibold">{formatE164(session.phone)}</Txt>
          </View>
        </Card>
        <Card>
          <View style={{ gap: 4 }} testID="profile-email">
            <View style={styles.emailTop}>
              <Txt size={fontSizes.sm} color={colors.muted}>
                Email
              </Txt>
              {session.email ? (
                <View style={[styles.badge, session.emailVerified ? styles.proved : styles.notProved]}>
                  <Txt size={12} weight="bold">
                    {session.emailVerified ? "Proved" : "Not proved yet"}
                  </Txt>
                </View>
              ) : null}
            </View>
            <Txt weight="semibold">{session.email ?? "No email address"}</Txt>
            <Txt size={fontSizes.sm} color={colors.muted}>
              {session.email
                ? session.emailVerified
                  ? "Statements and receipts are emailed here."
                  : "We email statements and receipts only to an address you have proved is yours."
                : "We email statements and receipts only to a proved address. Ask us in Help to add one."}
            </Txt>
            {session.email && !session.emailVerified ? (
              <View style={{ marginTop: 10, gap: 10 }}>
                {emailNote ? <Notice tone={emailNote.tone}>{emailNote.text}</Notice> : null}
                <Button
                  label={emailNote?.tone === "neutral" ? "Send another link" : "Email me a link"}
                  variant="secondary"
                  height={48}
                  onPress={sendLink}
                  loading={sending}
                  testID="send-email-proof"
                />
              </View>
            ) : null}
          </View>
        </Card>
        <Card padded={false}>
          <Row
            icon="shield"
            title="ID check and account number"
            detail="Confirm who you are, and see your account number"
            onPress={() => router.push("/id-check")}
            testID="profile-id-check"
          />
          <View style={styles.divider} />
          <Row
            icon="key"
            title="Sign in and security"
            detail="Passkey, authenticator app, SMS codes and devices"
            onPress={() => router.push("/security")}
            testID="open-security"
          />
          <View style={styles.divider} />
          <Row
            icon="send"
            title="Payout accounts"
            detail="Where we pay you, and your payouts"
            onPress={() => router.push("/payouts")}
            testID="open-payout-accounts"
          />
          <View style={styles.divider} />
          <Row
            icon="profile"
            title="Next of kin"
            detail="Who we contact if something happens to you"
            onPress={() => router.push("/account/next-of-kin")}
            testID="profile-next-of-kin"
          />
          <View style={styles.divider} />
          <Row
            icon="insights"
            title="About your investing"
            detail="Four questions so we suggest what suits you"
            onPress={() => router.push("/account/investing-questions")}
            testID="profile-investing-questions"
          />
          <View style={styles.divider} />
          <Row
            icon="download"
            title="Language and data"
            detail="App language, SMS receipts and a copy of your data"
            onPress={() => router.push("/account/language")}
            testID="profile-language"
          />
          <View style={styles.divider} />
          <Row
            icon="bell"
            title="Notifications"
            detail="What we told you, and how we tell you"
            onPress={() => router.push("/notifications")}
            testID="profile-notifications"
          />
          <View style={styles.divider} />
          <Row
            icon="message"
            title="Help"
            detail="Quick answers, chat with us and complaints"
            onPress={() => router.navigate("/help")}
            testID="profile-help"
          />
        </Card>
        {wide ? null : (
          <View style={{ flexDirection: "row", gap: 24 }}>
            <TextLink label="Portfolio" onPress={() => router.navigate("/portfolio")} />
            <TextLink label="Documents" onPress={() => router.navigate("/documents")} />
          </View>
        )}
        <Button label="Log out" onPress={signOut} loading={busy} testID="sign-out" />
        <View style={{ alignSelf: "center" }}>
          <TextLink label="Close my account" onPress={() => router.push("/account/close")} testID="profile-close" />
        </View>
      </View>
    </AppPage>
  );
}

type RowProps = {
  icon: IconName;
  title: string;
  detail: string;
  onPress: () => void;
  testID: string;
};

function Row({ icon, title, detail, onPress, testID }: RowProps) {
  return (
    <Pressable
      accessibilityRole="link"
      onPress={onPress}
      style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
      testID={testID}
    >
      <View style={styles.rowIcon}>
        <Icon name={icon} size={18} color={colors.black} />
      </View>
      <View style={{ flex: 1 }}>
        <Txt weight="bold">{title}</Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {detail}
        </Txt>
      </View>
      <Icon name="chevron" size={16} strokeWidth={2.2} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, minHeight: 68 },
  rowIcon: {
    width: 36,
    height: 36,
    borderRadius: 12,
    backgroundColor: colors.surfaceSunken,
    alignItems: "center",
    justifyContent: "center",
  },
  divider: { height: 1, backgroundColor: colors.border, marginHorizontal: 16 },
  emailTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  badge: { borderRadius: radii.pill, paddingVertical: 2, paddingHorizontal: 10 },
  proved: { backgroundColor: colors.lime },
  notProved: { backgroundColor: colors.surfaceSunken },
});
