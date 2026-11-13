/**
 * Finvest design tokens.
 *
 * Values come from the design canvas (see docs/design.md). Every value is a
 * plain string or number so React Native can use it directly. The web apps use
 * the same values through tokens.css, which is generated from this file.
 */

export const colors = {
  /** Brand black. Sidebar, primary buttons. */
  black: "#0D0D0D",
  /** Brand lime. The active item and the one highlighted action on a screen. */
  lime: "#B6EB0A",
  /** Darker lime for a border around lime on a light background. */
  limeBorder: "#6E8F00",
  /** Pale lime behind a positive label, such as "On track". */
  limeSurface: "#E9F7B5",
  /** Text on the pale lime surface. */
  limeText: "#2A3800",
  /** Body text. */
  ink: "#141414",
  /** Secondary text on light backgrounds. */
  muted: "#5E5E5A",
  /** Chart and accent colour that pairs with black. */
  aqua: "#18D0DC",
  white: "#FFFFFF",
  /** Page background. */
  paper: "#F4F5F0",
  /** Cards and inputs. */
  surface: "#FFFFFF",
  /** Selected rows and quiet icon tiles. */
  surfaceSunken: "#ECECE8",
  /** Card borders. */
  border: "#E3E6DD",
  /** Input and secondary button borders. */
  borderStrong: "#C9CEC0",
  /** Lines between rows inside a card. */
  divider: "#EEF0EA",
  /** Neutral status dot. */
  neutral: "#8C8C88",

  /** Raised surface on black, such as the avatar in the sidebar. */
  onBlackSurface: "#222222",
  /** Divider on black. */
  onBlackDivider: "#333333",
  /** Button border on black. */
  onBlackBorder: "#4D4D4D",
  /** Secondary text on black. */
  onBlackMuted: "#C9C9C4",

  warning: "#B45309",
  warningText: "#8A4B00",
  warningSurface: "#FDEFD8",
  danger: "#B42318",
  dangerSurface: "#FDECEA",
  success: "#1F7A3D",
  successSurface: "#E5F4EA",
} as const;

/** Spacing scale in density-independent pixels. */
export const spacing = {
  0: 0,
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  5: 20,
  6: 24,
  7: 28,
  8: 32,
  10: 40,
  12: 48,
  14: 56,
  16: 64,
} as const;

/** Corner radii in pixels. */
export const radii = {
  sm: 10,
  md: 12,
  lg: 16,
  xl: 20,
  pill: 999,
} as const;

export const fontFamily = "Plus Jakarta Sans";

/** Font sizes in pixels. */
export const fontSizes = {
  "2xs": 11,
  xs: 12,
  sm: 13,
  md: 14,
  lg: 16,
  xl: 18,
  "2xl": 20,
  "3xl": 26,
  "4xl": 30,
} as const;

export const fontWeights = {
  regular: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
  extrabold: 800,
} as const;

export const tokens = {
  colors,
  spacing,
  radii,
  fontFamily,
  fontSizes,
  fontWeights,
} as const;

export type ColorToken = keyof typeof colors;
export type SpacingToken = keyof typeof spacing;
export type RadiusToken = keyof typeof radii;
export type FontSizeToken = keyof typeof fontSizes;
export type FontWeightToken = keyof typeof fontWeights;
