import { describe, expect, it, vi } from "vitest";
import type { Http } from "@/api/http.ts";
import { STUB_ROLE_PERMISSIONS } from "@/auth/stub.ts";
import type { Role, StaffSession } from "@/auth/types.ts";
import { badge, createHttpMenuCountsApi, createStubMenuCountsApi } from "./counts.ts";
import { NAV_ITEMS } from "./nav.ts";

function sessionFor(role: Role): StaffSession {
  return { staffId: role, name: "Sample Person", role, permissions: STUB_ROLE_PERMISSIONS[role], secondSteps: [] };
}

describe("menu counts", () => {
  it("shows nothing at zero and stops at 99+", () => {
    expect(badge(undefined)).toBeNull();
    expect(badge(0)).toBeNull();
    expect(badge(7)).toBe("7");
    expect(badge(99)).toBe("99");
    expect(badge(100)).toBe("99+");
  });

  it("reads the counts the API sends", async () => {
    const request = vi.fn().mockResolvedValue({ counts: { "review-queue": 7, rates: 0 } });
    const http = { request, download: vi.fn() } as unknown as Http;
    expect(await createHttpMenuCountsApi(http).load()).toEqual({ "review-queue": 7, rates: 0 });
    expect(request).toHaveBeenCalledWith("GET", "/staff/menu-counts");
  });

  it("gives the sample counts only for screens the role can open", async () => {
    for (const role of ["admin", "operations", "finance", "compliance", "support"] as const) {
      const session = sessionFor(role);
      const counts = await createStubMenuCountsApi(session).load();
      for (const path of Object.keys(counts)) {
        const item = NAV_ITEMS.find((found) => found.path === path);
        expect(item && session.permissions.includes(item.permission)).toBe(true);
      }
    }
    expect(await createStubMenuCountsApi(sessionFor("admin")).load()).toMatchObject({ "review-queue": 7, compliance: 6 });
  });
});
