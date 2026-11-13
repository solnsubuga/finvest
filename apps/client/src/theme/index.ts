import {
  PlusJakartaSans_400Regular,
  PlusJakartaSans_500Medium,
  PlusJakartaSans_600SemiBold,
  PlusJakartaSans_700Bold,
  PlusJakartaSans_800ExtraBold,
} from "@expo-google-fonts/plus-jakarta-sans";

import { fontWeights } from "@finvest/tokens";

export { colors, fontSizes, fontWeights, radii, spacing } from "@finvest/tokens";

export type FontWeightName = keyof typeof fontWeights;

/** Font files to load at start-up, keyed by the family name React Native uses. */
export const fontAssets = {
  PlusJakartaSans_400Regular,
  PlusJakartaSans_500Medium,
  PlusJakartaSans_600SemiBold,
  PlusJakartaSans_700Bold,
  PlusJakartaSans_800ExtraBold,
};

const families: Record<FontWeightName, keyof typeof fontAssets> = {
  regular: "PlusJakartaSans_400Regular",
  medium: "PlusJakartaSans_500Medium",
  semibold: "PlusJakartaSans_600SemiBold",
  bold: "PlusJakartaSans_700Bold",
  extrabold: "PlusJakartaSans_800ExtraBold",
};

/**
 * React Native picks a font file by family name, not by weight, so every
 * weight of Plus Jakarta Sans is its own family.
 */
export function fontFor(weight: FontWeightName): string {
  return families[weight];
}

/** Screens at least this wide get the sidebar and the centred column. */
export const WIDE_BREAKPOINT = 900;

/** Width of the phone column that auth and detail screens keep on wide screens. */
export const COLUMN_WIDTH = 440;
