import { describe, expect, it } from "vitest";
import { NAV_ITEMS } from "@/nav/nav.ts";
import { SCREENS, screensFrom } from "./registry.ts";

describe("screen registry", () => {
  it("keys a screen by its folder name", () => {
    const Page = () => null;
    const load = async () => ({ default: Page });
    const screens = screensFrom({
      "./identity-checks/index.tsx": load,
      "./clients/index.tsx": load,
    });
    expect([...screens.keys()]).toEqual(["identity-checks", "clients"]);
    // Each screen is fetched only when it is first drawn.
    expect(screens.get("clients")).toBeDefined();
  });

  it("only holds screens that are in the menu", () => {
    const paths = new Set(NAV_ITEMS.map((item) => item.path));
    for (const path of SCREENS.keys()) {
      expect(paths, `src/screens/${path} does not match a path in src/nav/nav.ts`).toContain(path);
    }
  });
});
