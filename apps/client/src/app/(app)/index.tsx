import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { AppPage } from "@/components/AppPage";
import { Notice } from "@/components/Blocks";
import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors } from "@/theme";
import { useIsWide } from "@/theme/useIsWide";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

export default function Home() {
  const router = useRouter();
  const { session } = useAuth();
  const wide = useIsWide();
  const name = session?.displayName ?? null;
  const firstName = name?.split(/\s+/)[0];

  return (
    <AppPage
      title={firstName ? `Welcome, ${firstName}` : "Welcome to Finvest"}
      subtitle={wide ? "The same account as your phone, on a bigger screen." : undefined}
    >
      {session?.restricted ? (
        <Notice tone="warning">
          You signed in with an SMS code, so for 24 hours withdrawals only go to accounts you have used
          before.
        </Notice>
      ) : null}
      <Notice tone="neutral">
        Nothing here yet. Your investments, goals and activity will appear on this screen as soon as
        you start.
      </Notice>
      {wide ? null : (
        // The sidebar has Profile on a wide screen. On a phone it lives here.
        <Pressable
          accessibilityRole="link"
          accessibilityLabel="Profile"
          onPress={() => router.navigate("/profile")}
          style={({ pressed }) => [styles.profile, pressed && { opacity: 0.7 }]}
        >
          <View style={styles.avatar}>
            {name ? (
              <Txt size={15} weight="bold" color={colors.white}>
                {initials(name)}
              </Txt>
            ) : (
              <Icon name="profile" color={colors.white} />
            )}
          </View>
          <View style={{ flex: 1 }}>
            <Txt weight="bold">Profile</Txt>
            <Txt size={13} color={colors.muted}>
              Your details, documents and help
            </Txt>
          </View>
        </Pressable>
      )}
    </AppPage>
  );
}

const styles = StyleSheet.create({
  profile: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 20,
    padding: 14,
  },
  avatar: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.black,
    alignItems: "center",
    justifyContent: "center",
  },
});
