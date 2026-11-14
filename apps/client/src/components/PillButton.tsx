import { ActivityIndicator, Pressable, StyleSheet } from "react-native";

import { colors } from "@/theme";

import { Txt } from "./Txt";

/** A compact button sized to its words, for the heading row of a wide page. */
export function PillButton({
  label,
  onPress,
  primary = false,
  disabled = false,
  loading = false,
  testID,
}: {
  label: string;
  onPress: () => void;
  primary?: boolean;
  disabled?: boolean;
  loading?: boolean;
  testID?: string;
}) {
  const inactive = disabled || loading;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: inactive, busy: loading }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [styles.pill, primary ? styles.primary : styles.plain, inactive && { opacity: 0.5 }, pressed && { opacity: 0.8 }]}
      testID={testID}
    >
      {loading ? <ActivityIndicator size="small" color={colors.ink} /> : null}
      <Txt size={14} weight="bold">
        {label}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pill: { height: 44, paddingHorizontal: 18, borderRadius: 22, flexDirection: "row", alignItems: "center", gap: 8 },
  primary: { backgroundColor: colors.lime },
  plain: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.borderStrong },
});
