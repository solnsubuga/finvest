import { useEffect, useState } from "react";
import { apiBaseUrl, createHttp, type Http } from "@/api/http.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can, type StaffSession } from "@/auth/types.ts";
import { NAV_ITEMS } from "./nav.ts";

/**
 * How much work waits on each screen, for the numbers beside the menu (AdminNav on the canvas).
 * The backend counts only screens this person may open, up to 100, from GET /staff/menu-counts.
 * A screen with no count, or a count of zero, shows nothing.
 */
export type MenuCounts = Readonly<Partial<Record<string, number>>>;

export interface MenuCountsApi {
  load(): Promise<MenuCounts>;
}

/** Asked again this often, and whenever the person moves to another screen. */
export const REFRESH_MS = 60_000;

/** What the menu shows for a count: nothing at zero, "99+" past 99. */
export function badge(count: number | undefined): string | null {
  if (!count || count < 1) return null;
  return count > 99 ? "99+" : String(count);
}

export function createHttpMenuCountsApi(http: Http): MenuCountsApi {
  return {
    async load() {
      const body = await http.request<{ counts: Record<string, number> }>("GET", "/staff/menu-counts");
      return body.counts;
    },
  };
}

/** The canvas's numbers for `pnpm dev` and previews, for the screens this person may open. */
const SAMPLE: Readonly<Record<string, number>> = {
  "review-queue": 7,
  "identity-checks": 2,
  maturities: 2,
  support: 4,
  complaints: 5,
  reconciliation: 3,
  rates: 1,
  compliance: 6,
};

export function createStubMenuCountsApi(session: StaffSession): MenuCountsApi {
  return {
    load: () =>
      Promise.resolve(
        Object.fromEntries(
          NAV_ITEMS.filter((item) => item.path in SAMPLE && can(session, item.permission)).map((item) => [
            item.path,
            SAMPLE[item.path],
          ]),
        ),
      ),
  };
}

export function createMenuCountsApi(session: StaffSession): MenuCountsApi | null {
  if (isStubAuth()) return createStubMenuCountsApi(session);
  const base = apiBaseUrl();
  return base ? createHttpMenuCountsApi(createHttp(base)) : null;
}

/**
 * The counts, read when the menu appears, every minute after, and when `refreshKey` changes.
 * A failed read keeps the last counts: the menu still works without them.
 */
export function useMenuCounts(api: MenuCountsApi | null, refreshKey: string): MenuCounts {
  const [counts, setCounts] = useState<MenuCounts>({});
  useEffect(() => {
    if (!api) return;
    let live = true;
    const read = () => {
      api.load().then(
        (found) => {
          if (live) setCounts(found);
        },
        () => undefined,
      );
    };
    read();
    const timer = setInterval(read, REFRESH_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [api, refreshKey]);
  return counts;
}
