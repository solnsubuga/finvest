import { Pressable, StyleSheet, View } from "react-native";

import { PIN_LENGTH } from "@/auth/pin";
import { colors } from "@/theme";

import { Icon } from "../Icon";
import { Txt } from "../Txt";

/** The dots for a PIN being typed: lime for each digit so far. */
export function PinDots({ count, invalid = false }: { count: number; invalid?: boolean }) {
  return (
    <View
      style={styles.dots}
      // A progress bar, as StepProgress is: on the web a plain view may not carry a label.
      accessibilityRole="progressbar"
      accessibilityLabel={`${count} of ${PIN_LENGTH} digits entered`}
      accessibilityValue={{ min: 0, max: PIN_LENGTH, now: count }}
      testID="pin-dots"
    >
      {Array.from({ length: PIN_LENGTH }, (_, index) => (
        <View
          key={index}
          style={[
            styles.dot,
            index < count ? styles.filled : styles.empty,
            invalid && index >= count && { borderColor: colors.danger },
          ]}
        />
      ))}
    </View>
  );
}

const DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

/**
 * The keypad from the design: three by four, on black. The digits add to the
 * PIN and the last key takes one away. The PIN is never shown.
 */
export function PinPad({
  value,
  onChange,
  disabled = false,
}: {
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const press = (digit: string) => {
    if (value.length < PIN_LENGTH) onChange(value + digit);
  };
  return (
    <View style={styles.pad}>
      {DIGITS.map((digit) => (
        <Key key={digit} label={digit} onPress={() => press(digit)} disabled={disabled} />
      ))}
      <View style={styles.key} />
      <Key label="0" onPress={() => press("0")} disabled={disabled} />
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Delete"
        disabled={disabled || value.length === 0}
        onPress={() => onChange(value.slice(0, -1))}
        style={({ pressed }) => [styles.key, styles.plain, pressed && styles.pressed]}
        testID="pin-delete"
      >
        <Icon name="back" size={26} color={colors.white} strokeWidth={2.2} />
      </Pressable>
    </View>
  );
}

function Key({ label, onPress, disabled }: { label: string; onPress: () => void; disabled: boolean }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.key, styles.digit, pressed && styles.pressed]}
      testID={`pin-key-${label}`}
    >
      <Txt size={28} weight="bold" color={colors.white}>
        {label}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  dots: { flexDirection: "row", gap: 16, justifyContent: "center" },
  dot: { width: 16, height: 16, borderRadius: 8 },
  filled: { backgroundColor: colors.lime },
  empty: { borderWidth: 2, borderColor: colors.onBlackMuted },
  pad: {
    width: "100%",
    maxWidth: 320,
    alignSelf: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    rowGap: 14,
  },
  key: { width: 76, height: 76, borderRadius: 38, alignItems: "center", justifyContent: "center" },
  digit: { backgroundColor: colors.onBlackSurface },
  plain: { backgroundColor: "transparent" },
  pressed: { opacity: 0.7 },
});
