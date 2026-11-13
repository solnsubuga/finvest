import { describe, expect, it } from "vitest";
import { ACTION_PERMISSIONS } from "@/auth/types.ts";
import { NAV_ITEMS } from "@/nav/nav.ts";
import { permissionRows } from "./labels.ts";

describe("the role table", () => {
  it("has a row for every screen and every action the console knows", () => {
    const rows = permissionRows().map((row) => row.permission);
    for (const permission of ACTION_PERMISSIONS) expect(rows).toContain(permission);
    for (const item of NAV_ITEMS) expect(rows).toContain(item.permission);
    expect(new Set(rows).size).toBe(rows.length);
  });
});
