import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164 } from "@/auth/phone";
import { AppPage } from "@/components/AppPage";
import { Card } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
import { Icon, IconName } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors, COLUMN_WIDTH, fontSizes } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

export default function Profile() {
  const router = useRouter();
  const { client, session, setSession } = useAuth();
  const wide = useIsWide();
  const [busy, setBusy] = useState(false);

  if (!session) {
    return null;
  }

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
        <Card padded={false}>
          <Row
            icon="key"
            title="Sign in and security"
            detail="Passkey, authenticator app and SMS codes"
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
});
