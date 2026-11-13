import { useMemo } from "react";
import { View } from "react-native";
import createQr from "qrcode-generator";
import Svg, { Path } from "react-native-svg";

import { colors, radii } from "@/theme";

type Props = { value: string; size?: number; label: string };

/** One path with a 1x1 square for every dark module. */
export function qrPath(value: string): { path: string; count: number } {
  const code = createQr(0, "M");
  code.addData(value);
  code.make();
  const count = code.getModuleCount();
  let path = "";
  for (let row = 0; row < count; row += 1) {
    for (let column = 0; column < count; column += 1) {
      if (code.isDark(row, column)) {
        path += `M${column} ${row}h1v1h-1z`;
      }
    }
  }
  return { path, count };
}

/**
 * A QR code drawn on the device. The value never leaves the app to be turned
 * into an image somewhere else, which matters because it can hold a key.
 */
export function QrCode({ value, size = 184, label }: Props) {
  const { path, count } = useMemo(() => qrPath(value), [value]);
  // Scanners need a quiet border of four modules around the code.
  const quiet = 4;
  const box = count + quiet * 2;
  return (
    <View
      accessible
      accessibilityRole="image"
      accessibilityLabel={label}
      testID="qr-code"
      style={{ backgroundColor: colors.white, borderRadius: radii.md, overflow: "hidden" }}
    >
      <Svg width={size} height={size} viewBox={`${-quiet} ${-quiet} ${box} ${box}`}>
        <Path d={path} fill={colors.black} />
      </Svg>
    </View>
  );
}
