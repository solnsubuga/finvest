import { Pressable, StyleSheet, View } from "react-native";

import { colors, fontSizes } from "@/theme";

import { Txt } from "./Txt";

/**
 * A row with a title, a line about it and an on/off switch, as drawn on the
 * Language and data screen. The whole row is the switch, so it is easy to hit.
 */
export function ToggleRow({
  title,
  detail,
  on,
  onChange,
  disabled,
  testID,
}: {
  title: string;
  detail: string;
  on: boolean;
  onChange: (on: boolean) => void;
  disabled?: boolean;
  testID?: string;
}) {
  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityLabel={title}
      accessibilityHint={detail}
      accessibilityState={{ checked: on, disabled: !!disabled }}
      aria-checked={on}
      disabled={disabled}
      onPress={() => onChange(!on)}
      style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
      testID={testID}
    >
      <View style={{ flex: 1, gap: 2 }}>
        <Txt weight="bold">{title}</Txt>
        <Txt size={fontSizes.sm} color={colors.muted}>
          {detail}
        </Txt>
      </View>
      <View style={[styles.track, on ? styles.trackOn : styles.trackOff]}>
        <View style={[styles.knob, { backgroundColor: on ? colors.lime : colors.white }]} />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12, minHeight: 64 },
  track: { width: 52, height: 32, borderRadius: 16, padding: 3, flexDirection: "row", alignItems: "center" },
  // The off track is dark enough to see against the white card (3:1 for a control).
  trackOff: { backgroundColor: colors.neutral, justifyContent: "flex-start" },
  trackOn: { backgroundColor: colors.black, justifyContent: "flex-end" },
  knob: { width: 26, height: 26, borderRadius: 13 },
});
