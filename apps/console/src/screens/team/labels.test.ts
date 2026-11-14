import { describe, expect, it } from "vitest";
import { ACTION_PERMISSIONS } from "@/auth/types.ts";
import { NAV_ITEMS } from "@/nav/nav.ts";
import { accessLabel, permissionRows } from "./labels.ts";

describe("the role table", () => {
  it("has a row for every screen and every action the console knows", () => {
    const rows = permissionRows().map((row) => row.permission);
    for (const permission of ACTION_PERMISSIONS) expect(rows).toContain(permission);
    for (const item of NAV_ITEMS) expect(rows).toContain(item.permission);
    expect(new Set(rows).size).toBe(rows.length);
  });
});

describe("Cloudflare Access on the team", () => {
  const at = "2026-11-13T08:00:00Z";
  it("says whether each person can reach the console", () => {
    expect(accessLabel({ status: "active", access: { state: "in_access", errorCode: null, requestedAt: at, syncedAt: at } }).text).toBe("Can reach the console");
    expect(accessLabel({ status: "disabled", access: { state: "removed", errorCode: null, requestedAt: at, syncedAt: at } }).text).toBe("Removed from Access");
    expect(accessLabel({ status: "disabled", access: { state: "waiting", errorCode: null, requestedAt: at, syncedAt: null } }).text).toBe("Removing from Access");
    expect(accessLabel({ status: "invited", access: { state: "waiting", errorCode: null, requestedAt: at, syncedAt: null } }).text).toBe("Adding to Access");
    expect(accessLabel({ status: "active", access: null }).tone).toBe("quiet");
  });

  it("explains a failure in words, never the raw code", () => {
    const failed = accessLabel({ status: "active", access: { state: "failed", errorCode: "cloudflare_token_refused", requestedAt: at, syncedAt: null } });
    expect(failed.tone).toBe("bad");
    expect(failed.detail).toMatch(/refused the token/);
    const odd = accessLabel({ status: "active", access: { state: "failed", errorCode: "something_new", requestedAt: at, syncedAt: null } });
    expect(odd.detail).toBe("Cloudflare did not finish it.");
    const retrying = accessLabel({ status: "active", access: { state: "waiting", errorCode: "cloudflare_unavailable", requestedAt: at, syncedAt: null } });
    expect(retrying.detail).toBe("Cloudflare did not answer. Trying again.");
  });
});
