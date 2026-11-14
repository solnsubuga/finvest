import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  ViewStyle,
} from "react-native";

import { colors, fontSizes } from "@/theme";

import { Icon, IconName } from "./Icon";
import { Txt } from "./Txt";

type Variant = "primary" | "lime" | "secondary" | "outlineOnDark";

type Props = {
  label: string;
  onPress: () => void;
  variant?: Variant;
  disabled?: boolean;
  loading?: boolean;
  height?: number;
  /** A line icon before the words. Decorative: the label names the button. */
  icon?: IconName;
  testID?: string;
};

const fills: Record<
  Variant,
  { background: string; text: string; border?: string }
> = {
  primary: { background: colors.black, text: colors.white },
  lime: { background: colors.lime, text: colors.ink },
  secondary: {
    background: colors.surface,
    text: colors.ink,
    border: colors.borderStrong,
  },
  outlineOnDark: {
    background: "transparent",
    text: colors.white,
    border: colors.white,
  },
};

export function Button({
  label,
  onPress,
  variant = "primary",
  disabled = false,
  loading = false,
  height = 56,
  icon,
  testID,
}: Props) {
  const fill = fills[variant];
  const inactive = disabled || loading;
  const shape: ViewStyle = {
    height,
    borderRadius: height / 2,
    backgroundColor: fill.background,
    borderWidth: fill.border ? 2 : 0,
    borderColor: fill.border,
  };
  return (
    <Pressable
      accessibilityRole="button"
      // While it is busy the spinner replaces the words, so the name is given here.
      accessibilityLabel={label}
      aria-disabled={inactive}
      aria-busy={loading}
      disabled={inactive}
      onPress={onPress}
      testID={testID}
      style={({ pressed }) => [
        styles.base,
        shape,
        disabled && styles.disabled,
        pressed && styles.pressed,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={fill.text} aria-hidden />
      ) : (
        <>
          {icon ? <Icon name={icon} size={18} color={fill.text} /> : null}
          <Txt size={fontSizes.lg} weight="bold" color={fill.text}>
            {label}
          </Txt>
        </>
      )}
    </Pressable>
  );
}

type LinkProps = {
  label: string;
  onPress: () => void;
  onDark?: boolean;
  underline?: boolean;
  testID?: string;
};

/** A text link with a full 44 point touch target. */
export function TextLink({
  label,
  onPress,
  onDark,
  underline = true,
  testID,
}: LinkProps) {
  return (
    <Pressable
      accessibilityRole="link"
      onPress={onPress}
      testID={testID}
      style={({ pressed }) => [styles.link, pressed && styles.pressed]}
    >
      <Txt
        size={14}
        weight="semibold"
        color={onDark ? colors.white : colors.black}
        style={underline ? { textDecorationLine: "underline" } : undefined}
      >
        {label}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: { flexDirection: "row", gap: 8, alignItems: "center", justifyContent: "center", flexShrink: 0 },
  disabled: { opacity: 0.4 },
  pressed: { opacity: 0.75 },
  link: { minHeight: 44, justifyContent: "center", alignItems: "center" },
});
