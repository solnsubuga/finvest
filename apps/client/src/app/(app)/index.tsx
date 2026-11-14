import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { useSecondSteps } from "@/auth/useSecondSteps";
import { AppPage } from "@/components/AppPage";
import { Bell } from "@/components/Bell";
import { Notice } from "@/components/Blocks";
import { Icon } from "@/components/Icon";
import { LinkCard } from "@/components/LinkCard";
import { Txt } from "@/components/Txt";
import { IdCheckCard } from "@/identity/IdCheckCard";
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
  const steps = useSecondSteps();
  // Shown only when we know SMS is the one second step. If the list cannot be
  // loaded, Home stays quiet about it: Sign in and security reports the error.
  const onlySms = steps.status === "ready" && steps.methods.length === 1;
  const name = session?.displayName ?? null;
  const firstName = name?.split(/\s+/)[0];

  return (
    <AppPage
      title={firstName ? `Welcome, ${firstName}` : "Welcome to Finvest"}
      subtitle={
        wide ? "The same account as your phone, on a bigger screen." : undefined
      }
      action={<Bell />}
    >
      {session?.restricted ? (
        <Notice tone="warning">
          You signed in with a code, so for 24 hours withdrawals only go to
          accounts you have used before.
        </Notice>
      ) : null}
      <IdCheckCard />
      {onlySms ? (
        <Pressable
          accessibilityRole="link"
          onPress={() => router.push("/security")}
          style={({ pressed }) => [styles.profile, pressed && { opacity: 0.7 }]}
          testID="security-prompt"
        >
          <View style={[styles.avatar, { borderRadius: 14 }]}>
            <Icon name="key" color={colors.lime} />
          </View>
          <View style={{ flex: 1 }}>
            <Txt weight="bold">Make signing in safer</Txt>
            <Txt size={13} color={colors.muted}>
              Add a passkey or an authenticator app, so you do not depend on SMS
              codes.
            </Txt>
          </View>
          <Icon name="chevron" size={16} strokeWidth={2.2} />
        </Pressable>
      ) : null}
      <Notice tone="neutral">
        Nothing here yet. Your investments, goals and activity will appear on
        this screen as soon as you start.
      </Notice>
      <LinkCard
        icon="repeat"
        title="Invest automatically"
        description="The same amount every week or month, from your mobile money"
        onPress={() => router.push("/autoinvest")}
        testID="home-autoinvest"
      />
      {wide ? null : (
        // The sidebar has Insights on a wide screen. On a phone it lives here.
        <LinkCard
          icon="insights"
          title="Insights"
          description="What your money earned, month by month"
          onPress={() => router.navigate("/insights")}
          testID="home-insights"
        />
      )}
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
