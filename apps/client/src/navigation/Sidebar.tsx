import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { formatE164 } from "@/auth/phone";
import { Session } from "@/auth/types";
import { Logo } from "@/components/Logo";
import { Txt } from "@/components/Txt";
import { colors, radii } from "@/theme";

import { destinations } from "./destinations";

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
      {destinations.map((destination) => {
        const active = destination.name === activeName;
        return (
          <Pressable
            key={destination.name}
            accessibilityRole="link"
            accessibilityState={{ selected: active }}
            aria-current={active ? "page" : undefined}
            onPress={() => router.navigate(destination.href)}
            style={({ pressed, hovered }: { pressed: boolean; hovered?: boolean }) => [
              styles.item,
              active && styles.itemActive,
              !active && (pressed || hovered) && styles.itemHover,
            ]}
          >
            <Txt size={14} weight={active ? "bold" : "semibold"} color={active ? colors.ink : colors.white}>
              {destination.label}
            </Txt>
          </Pressable>
        );
      })}
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
    gap: 4,
  },
  logo: { paddingHorizontal: 8, paddingBottom: 16 },
  item: {
    height: 44,
    paddingHorizontal: 14,
    borderRadius: radii.md,
    justifyContent: "center",
  },
  itemActive: { backgroundColor: colors.lime },
  itemHover: { backgroundColor: colors.onBlackSurface },
  account: {
    marginTop: "auto",
    borderTopWidth: 1,
    borderTopColor: colors.onBlackDivider,
    paddingTop: 14,
    paddingHorizontal: 8,
  },
});
