import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { toCss } from "./css.ts";
import { colors, fontSizes, radii, spacing } from "./index.ts";

describe("tokens", () => {
  it("keeps the brand colours from docs/design.md", () => {
    expect(colors.black).toBe("#0D0D0D");
    expect(colors.lime).toBe("#B6EB0A");
    expect(colors.ink).toBe("#141414");
    expect(colors.muted).toBe("#5E5E5A");
  });

  it("writes every colour as a six digit hex value", () => {
    for (const value of Object.values(colors)) {
      expect(value).toMatch(/^#[0-9A-F]{6}$/);
    }
  });

  it("keeps sizes as plain numbers so React Native can use them", () => {
    for (const scale of [spacing, radii, fontSizes]) {
      for (const value of Object.values(scale)) {
        expect(Number.isInteger(value)).toBe(true);
      }
    }
  });

  it("has a tokens.css that matches the TypeScript source", () => {
    const committed = readFileSync(new URL("../tokens.css", import.meta.url), "utf8");
    expect(committed).toBe(toCss());
  });
});
