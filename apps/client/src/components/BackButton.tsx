import { Pressable, StyleSheet } from "react-native";
import { Href, useRouter } from "expo-router";

import { colors } from "@/theme";

import { Icon } from "./Icon";

/** Goes back, or to `fallback` when the screen was opened directly on the web. */
export function BackButton({ fallback }: { fallback: Href }) {
  const router = useRouter();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel="Back"
      onPress={() => (router.canGoBack() ? router.back() : router.replace(fallback))}
      style={({ pressed }) => [styles.button, pressed && { opacity: 0.7 }]}
    >
      <Icon name="back" strokeWidth={2.2} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
});
