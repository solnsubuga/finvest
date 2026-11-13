import { ComponentRef, useRef, useState } from "react";
import { Platform, Pressable, StyleSheet, TextInput, View } from "react-native";

import { colors, fontFor } from "@/theme";

import { Txt } from "./Txt";

export const CODE_LENGTH = 6;

type Props = {
  value: string;
  onChangeText: (code: string) => void;
  invalid?: boolean;
  autoFocus?: boolean;
};

/**
 * Six boxes backed by one real text input, so SMS autofill, paste and screen
 * readers all see a single field instead of six.
 */
export function CodeInput({ value, onChangeText, invalid = false, autoFocus = true }: Props) {
  const input = useRef<ComponentRef<typeof TextInput>>(null);
  const [focused, setFocused] = useState(false);
  const active = Math.min(value.length, CODE_LENGTH - 1);

  return (
    <Pressable onPress={() => input.current?.focus()} style={styles.row} accessible={false}>
      {Array.from({ length: CODE_LENGTH }, (_, index) => (
        <View
          key={index}
          style={[
            styles.box,
            focused && index === active && styles.boxActive,
            invalid && styles.boxInvalid,
          ]}
        >
          <Txt size={24} weight="bold">
            {value[index] ?? ""}
          </Txt>
        </View>
      ))}
      <TextInput
        ref={input}
        accessibilityLabel="6 digit code"
        testID="code-input"
        value={value}
        onChangeText={(text) => onChangeText(text.replace(/\D/g, "").slice(0, CODE_LENGTH))}
        keyboardType="number-pad"
        inputMode="numeric"
        autoComplete="one-time-code"
        textContentType="oneTimeCode"
        maxLength={CODE_LENGTH}
        autoFocus={autoFocus}
        caretHidden
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={[styles.input, Platform.OS === "web" ? ({ outlineStyle: "none" } as object) : null]}
      />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: 8 },
  box: {
    flex: 1,
    height: 60,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  boxActive: { borderWidth: 2, borderColor: colors.black },
  boxInvalid: { borderWidth: 2, borderColor: colors.danger },
  // Covers the boxes and stays invisible: it receives the taps and the typing.
  input: {
    position: "absolute",
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    color: "transparent",
    fontFamily: fontFor("bold"),
    fontSize: 24,
    opacity: 0.02,
  },
});
