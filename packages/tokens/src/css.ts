import { colors, fontFamily, fontSizes, fontWeights, radii, spacing } from "./index.ts";

const kebab = (name: string): string =>
  name.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();

const block = (prefix: string, values: Record<string, string | number>, unit = ""): string[] =>
  Object.entries(values).map(
    ([name, value]) => `  --fv-${prefix}-${kebab(name)}: ${value}${unit};`,
  );

/** The contents of tokens.css: every token as a CSS custom property. */
export function toCss(): string {
  const lines = [
    "/* Generated from src/index.ts by `pnpm build:css`. Do not edit by hand. */",
    ":root {",
    ...block("color", colors),
    ...block("space", spacing, "px"),
    ...block("radius", radii, "px"),
    `  --fv-font-family: "${fontFamily}", ui-sans-serif, system-ui, sans-serif;`,
    ...block("font-size", fontSizes, "px"),
    ...block("font-weight", fontWeights),
    "}",
    "",
  ];
  return lines.join("\n");
}
