import { describe, expect, it } from "vitest";
import { STUB_ROLE_PERMISSIONS } from "@/auth/stub.ts";
import { ROLES, SCREEN_PERMISSIONS, type Role, type StaffSession } from "@/auth/types.ts";
import { homePath, NAV_GROUPS, NAV_ITEMS, visibleNav } from "./nav.ts";

function sessionFor(role: Role): StaffSession {
  return { staffId: role, name: "Sample Person", role, permissions: STUB_ROLE_PERMISSIONS[role], secondSteps: [] };
}

const labels = (session: StaffSession) =>
  visibleNav(session).flatMap((group) => group.items.map((item) => item.label));

describe("console menu", () => {
  it("has the four groups from the canvas, in order", () => {
    expect(NAV_GROUPS.map((group) => group.title)).toEqual([
      "Daily work",
      "Clients and money",
      "Catalogue",
      "Control",
    ]);
  });

  it("gives every screen its own path and its own permission", () => {
    expect(new Set(NAV_ITEMS.map((item) => item.path)).size).toBe(NAV_ITEMS.length);
    expect(NAV_ITEMS.map((item) => item.permission).sort()).toEqual([...SCREEN_PERMISSIONS].sort());
  });

  it("shows an admin every screen", () => {
    expect(labels(sessionFor("admin"))).toEqual(NAV_ITEMS.map((item) => item.label));
  });

  it("shows support only what support works on, and drops empty groups", () => {
    const nav = visibleNav(sessionFor("support"));
    expect(nav.map((group) => group.title)).toEqual(["Daily work", "Clients and money"]);
    expect(labels(sessionFor("support"))).toEqual([
      "Overview",
      "Support and Messages",
      "Complaints",
      "Clients",
    ]);
  });

  it("keeps team away from non-admin roles, and settings from all but admin, finance and operations", () => {
    for (const role of ROLES.filter((r) => r !== "admin")) {
      const seen = labels(sessionFor(role));
      expect(seen).not.toContain("Team and Permissions");
      // Finance opens Settings for the bank account and the limits, operations to pause a channel or keep the calendar.
      if (role === "finance" || role === "operations") expect(seen).toContain("Settings");
      else expect(seen).not.toContain("Settings");
    }
  });

  it("shows nothing to someone with no permissions", () => {
    const nobody: StaffSession = { staffId: "x", name: "No One", role: "support", permissions: [], secondSteps: [] };
    expect(visibleNav(nobody)).toEqual([]);
    expect(homePath(nobody)).toBeNull();
  });

  it("sends each role to the first screen it can open", () => {
    for (const role of ROLES) {
      expect(homePath(sessionFor(role))).toBe("/overview");
    }
  });
});
