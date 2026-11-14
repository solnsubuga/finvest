import { Pressable, ScrollView, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164 } from "@/auth/phone";
import { Session } from "@/auth/types";
import { Icon } from "@/components/Icon";
import { Logo } from "@/components/Logo";
import { Txt } from "@/components/Txt";
import { colors, radii } from "@/theme";

import { sidebarGroups } from "./destinations";

type Props = { activeName: string; session: Session };

/** The black left sidebar for wide screens. */
export function Sidebar({ activeName, session }: Props) {
  const router = useRouter();
  const { demo } = useAuth();
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
        <Txt size={12} color={colors.onBlackMuted}>
          Signed in as
        </Txt>
        <Txt size={13} weight="bold" color={colors.white}>
          {session.displayName ?? formatE164(session.phone)}
        </Txt>
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
});
