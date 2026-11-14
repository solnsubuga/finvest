import { Pressable, StyleSheet, View } from "react-native";

import { colors, fontSizes } from "@/theme";

import { Txt } from "./Txt";

type Props = {
  label: string;
  detail?: string;
  selected: boolean;
  onPress?: () => void;
  disabled?: boolean;
  testID?: string;
};

/** One answer among several, as a card with a round mark on the right. */
export function Choice({ label, detail, selected, onPress, disabled = false, testID }: Props) {
  return (
    <Pressable
      accessibilityRole="radio"
      aria-checked={selected}
      aria-disabled={disabled}
      disabled={disabled}
      onPress={onPress}
      testID={testID}
      style={({ pressed }) => [
        styles.option,
        selected && styles.optionOn,
        disabled && { opacity: 0.55 },
        pressed && { opacity: 0.8 },
      ]}
    >
      <View style={{ flex: 1 }}>
        <Txt weight="bold">{label}</Txt>
        {detail ? (
          <Txt size={fontSizes.sm} color={colors.muted}>
            {detail}
          </Txt>
        ) : null}
      </View>
      <View style={[styles.radio, selected && styles.radioOn]}>
        {selected ? <View style={styles.radioDot} /> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.white,
    minHeight: 56,
  },
  optionOn: { borderWidth: 2, borderColor: colors.black, paddingVertical: 13, paddingHorizontal: 15 },
  radio: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 2,
    borderColor: colors.neutral,
    alignItems: "center",
    justifyContent: "center",
  },
  radioOn: { backgroundColor: colors.black, borderColor: colors.black },
  radioDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.lime },
});
