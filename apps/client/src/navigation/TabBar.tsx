import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Icon } from "@/components/Icon";
import { Txt } from "@/components/Txt";
import { colors } from "@/theme";

import { tabBarDestinations } from "./destinations";

/** The four-tab bar at the bottom of a phone screen. */
export function TabBar({ activeName }: { activeName: string }) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  return (
    <View
      style={[styles.bar, { paddingBottom: Math.max(insets.bottom, 12) }]}
      accessibilityRole="tablist"
    >
      {tabBarDestinations.map((destination) => {
        const active = destination.name === activeName;
        const color = active ? colors.black : colors.muted;
        return (
          <Pressable
            key={destination.name}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            accessibilityLabel={destination.label}
            onPress={() => router.navigate(destination.href)}
            style={({ pressed }) => [styles.tab, pressed && { opacity: 0.6 }]}
          >
            <Icon name={destination.icon} size={22} color={color} strokeWidth={active ? 2.2 : 2} />
            <Txt size={11} weight={active ? "bold" : "semibold"} color={color} lineHeight={14}>
              {destination.label}
            </Txt>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: "row",
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 8,
    paddingHorizontal: 8,
  },
  tab: { flex: 1, height: 48, alignItems: "center", justifyContent: "center", gap: 4 },
});
