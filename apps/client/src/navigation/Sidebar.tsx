import { Pressable, ScrollView, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164 } from "@/auth/phone";
import { Session } from "@/auth/types";
import { useSignOut } from "@/auth/useSignOut";
import { Icon } from "@/components/Icon";
import { Logo } from "@/components/Logo";
import { initials } from "@/profile/initials";
import { Txt } from "@/components/Txt";
import { colors, radii } from "@/theme";

import { SIDEBAR_ACCOUNT_DESTINATION, sidebarGroups } from "./destinations";
import { useStartLoaderDone } from "./useStartLoaderDone";

type Props = { activeName: string; session: Session };

/** The black left sidebar for wide screens. */
export function Sidebar({ activeName, session }: Props) {
  const router = useRouter();
  const { demo } = useAuth();
  const { signOut, busy } = useSignOut();
  useStartLoaderDone();
  const onProfile = activeName === SIDEBAR_ACCOUNT_DESTINATION;
  const name = session.displayName ?? formatE164(session.phone);
  return (
    <View style={styles.sidebar} accessibilityRole="navigation" accessibilityLabel="Main">
      <View style={styles.logo}>
        <Logo size={32} onDark />
      </View>
      {/* The menu scrolls on a short window, so the signed-in name stays in view. */}
      <ScrollView style={styles.menu} contentContainerStyle={styles.menuContent}>
        {sidebarGroups.map((group) => (
          <View key={group.key} style={styles.group} testID={`sidebar-group-${group.key}`}>
            {group.heading ? (
              <Txt
                accessibilityRole="header"
                size={11}
                weight="bold"
                color={colors.onBlackMuted}
                style={styles.heading}
              >
                {group.heading}
              </Txt>
            ) : null}
            {group.destinations.map((destination) => {
              const active = destination.name === activeName;
              return (
                <Pressable
                  key={destination.name}
                  accessibilityRole="link"
                  aria-current={active ? "page" : undefined}
                  onPress={() => router.navigate(destination.href)}
                  style={({ pressed, hovered }: { pressed: boolean; hovered?: boolean }) => [
                    styles.item,
                    active && styles.itemActive,
                    !active && (pressed || hovered) && styles.itemHover,
                  ]}
                >
                  <Icon
                    name={destination.icon}
                    size={18}
                    color={active ? colors.ink : colors.onBlackMuted}
                    strokeWidth={active ? 2.2 : 2}
                  />
                  <Txt
                    size={14}
                    weight={active ? "bold" : "semibold"}
                    color={active ? colors.ink : colors.white}
                  >
                    {destination.label}
                  </Txt>
                </Pressable>
              );
            })}
          </View>
        ))}
      </ScrollView>
      <View style={styles.account}>
        {demo ? (
          <Txt size={12} weight="bold" color={colors.lime}>
            Demo account
          </Txt>
        ) : null}
        <Pressable
          accessibilityRole="link"
          accessibilityLabel={`Profile, signed in as ${name}`}
          aria-current={onProfile ? "page" : undefined}
          onPress={() => router.navigate("/profile")}
          style={({ pressed, hovered }: { pressed: boolean; hovered?: boolean }) => [
            styles.me,
            onProfile && styles.itemActive,
            !onProfile && (pressed || hovered) && styles.itemHover,
          ]}
          testID="sidebar-profile"
        >
          <View style={[styles.mini, onProfile && styles.miniActive]}>
            {session.displayName ? (
              <Txt size={12} weight="extrabold" color={onProfile ? colors.lime : colors.ink}>
                {initials(session.displayName)}
              </Txt>
            ) : (
              <Icon name="profile" size={16} color={onProfile ? colors.lime : colors.ink} />
            )}
          </View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Txt size={13} weight="bold" color={onProfile ? colors.ink : colors.white} numberOfLines={1}>
              {name}
            </Txt>
            <Txt size={12} color={onProfile ? colors.ink : colors.onBlackMuted}>
              View your profile
            </Txt>
          </View>
          <Icon name="chevron" size={16} strokeWidth={2.2} color={onProfile ? colors.ink : colors.onBlackMuted} />
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Log out"
          accessibilityState={{ busy, disabled: busy }}
          disabled={busy}
          onPress={signOut}
          style={({ pressed, hovered }: { pressed: boolean; hovered?: boolean }) => [
            styles.logOut,
            (pressed || hovered) && styles.itemHover,
          ]}
          testID="sidebar-sign-out"
        >
          <Icon name="logout" size={18} color={colors.onBlackMuted} />
          <Txt size={14} weight="semibold" color={colors.white}>
            {busy ? "Logging out" : "Log out"}
          </Txt>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  sidebar: {
    width: 220,
    backgroundColor: colors.black,
    paddingVertical: 24,
    paddingHorizontal: 16,
    gap: 18,
  },
  logo: { paddingHorizontal: 8 },
  menu: { flex: 1 },
  menuContent: { gap: 18 },
  group: { gap: 2 },
  heading: {
    paddingHorizontal: 14,
    paddingBottom: 6,
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  item: {
    height: 40,
    paddingHorizontal: 14,
    borderRadius: radii.md,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  itemActive: { backgroundColor: colors.lime },
  itemHover: { backgroundColor: colors.onBlackSurface },
  account: {
    borderTopWidth: 1,
    borderTopColor: colors.onBlackDivider,
    paddingTop: 14,
    paddingHorizontal: 8,
  },
  me: {
    marginTop: 6,
    marginHorizontal: -8,
    padding: 8,
    borderRadius: radii.md,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  mini: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
  },
  miniActive: { backgroundColor: colors.black },
  logOut: {
    height: 40,
    marginTop: 10,
    marginHorizontal: -6,
    paddingHorizontal: 6,
    borderRadius: radii.md,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
});
