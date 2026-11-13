import { Text, TextProps, TextStyle } from "react-native";

import { colors, fontFor, fontSizes, FontWeightName } from "@/theme";

type Props = TextProps & {
  size?: number;
  weight?: FontWeightName;
  color?: string;
  lineHeight?: number;
  center?: boolean;
};

/** Text in Plus Jakarta Sans. Every piece of copy in the app goes through it. */
export function Txt({
  size = 15,
  weight = "regular",
  color = colors.ink,
  lineHeight,
  center,
  style,
  ...rest
}: Props) {
  const base: TextStyle = {
    fontFamily: fontFor(weight),
    fontSize: size,
    color,
    lineHeight: lineHeight ?? Math.round(size * 1.45),
    textAlign: center ? "center" : undefined,
  };
  return <Text {...rest} style={[base, style]} />;
}

export function Title({ children, onDark }: { children: string; onDark?: boolean }) {
  return (
    <Txt
      accessibilityRole="header"
      size={fontSizes["4xl"]}
      weight="extrabold"
      lineHeight={34}
      color={onDark ? colors.white : colors.ink}
      style={{ letterSpacing: -0.8 }}
    >
      {children}
    </Txt>
  );
}

export function Eyebrow({ children }: { children: string }) {
  return (
    <Txt
      size={fontSizes.xs}
      weight="bold"
      color={colors.muted}
      style={{ textTransform: "uppercase", letterSpacing: 0.6 }}
    >
      {children}
    </Txt>
  );
}
