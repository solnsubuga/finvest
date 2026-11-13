import { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164 } from "@/auth/phone";
import { AppPage } from "@/components/AppPage";
import { Card } from "@/components/Blocks";
import { Button, TextLink } from "@/components/Button";
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
        {wide ? null : (
          <View style={{ flexDirection: "row", gap: 24 }}>
            <TextLink label="Portfolio" onPress={() => router.navigate("/portfolio")} />
            <TextLink label="Documents" onPress={() => router.navigate("/documents")} />
            <TextLink label="Help" onPress={() => router.navigate("/help")} />
          </View>
        )}
        <Button label="Log out" onPress={signOut} loading={busy} testID="sign-out" />
      </View>
    </AppPage>
  );
}
