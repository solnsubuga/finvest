import { View } from "react-native";
import Svg, { Circle, Path, Rect } from "react-native-svg";

import { colors } from "@/theme";

import { Txt } from "./Txt";

type MarkProps = { size?: number; onDark?: boolean };

/** The mark: a white rising line that ends in a lime dot, on a black rounded square. */
export function LogoMark({ size = 36, onDark = false }: MarkProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64" accessibilityRole="image" aria-label="Finvest">
      <Rect width={64} height={64} rx={18} fill={onDark ? colors.onBlackSurface : colors.black} />
      <Path
        d="M14 44l11-12 9 8 9-11"
        fill="none"
        stroke={colors.white}
        strokeWidth={7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <Circle cx={49} cy={20} r={6.5} fill={colors.lime} />
    </Svg>
  );
}

export function Logo({ size = 36, onDark = false }: MarkProps) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
      <LogoMark size={size} onDark={onDark} />
      <Txt
        size={Math.round(size * 0.6)}
        weight="extrabold"
        color={onDark ? colors.white : colors.ink}
        style={{ letterSpacing: -0.5 }}
      >
        finvest
      </Txt>
    </View>
  );
}
