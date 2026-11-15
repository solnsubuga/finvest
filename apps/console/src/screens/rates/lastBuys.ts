/**
 * The latest bills Finvest bought for each product (GET /staff/dealing/last-buys).
 *
 * The backend holds no Bank of Uganda cut-off rates, so the "Last bought" tile shows the
 * yearly yield of Finvest's own latest settled purchase, before tax. The yield is worked out
 * on the server in basis points; nothing here does arithmetic on money.
 */

import type { LastBuysOut } from "@finvest/api-client/staff";
import { apiBaseUrl, createHttp, type Http } from "@/api/http.ts";

export interface LastBuy {
  productCode: string;
  atAuction: boolean;
  settledOn: string;
  maturesOn: string;
  /** Yearly yield before tax, in basis points. Null when the server could not work it out. */
  yieldBps: number | null;
}

export type LastBuysReader = () => Promise<readonly LastBuy[]>;

export function createHttpLastBuys(http: Http): LastBuysReader {
  return async () => {
    const json = await http.request<LastBuysOut>("GET", "/staff/dealing/last-buys");
    return json.buys.map((buy) => ({
      productCode: buy.product_code,
      atAuction: buy.at_auction,
      settledOn: buy.settled_on,
      maturesOn: buy.matures_on,
      yieldBps: buy.yearly_yield_bps,
    }));
  };
}

export function connectedLastBuys(): LastBuysReader | undefined {
  const base = apiBaseUrl();
  return base ? createHttpLastBuys(createHttp(base)) : undefined;
}

/** The newest buy across every product: the latest settled day, then the product code. */
export function latestBuy(buys: readonly LastBuy[]): LastBuy | undefined {
  return [...buys].sort((a, b) => b.settledOn.localeCompare(a.settledOn) || a.productCode.localeCompare(b.productCode))[0];
}

/** Sample buys for `pnpm dev`. `?buys=empty` has none and `?buys=error` cannot be read. */
export function stubLastBuys(sample: string | null): LastBuysReader {
  const buys: LastBuy[] =
    sample === "empty"
      ? []
      : [
          { productCode: "TREA-91", atAuction: false, settledOn: "2026-11-06", maturesOn: "2027-02-05", yieldBps: 1185 },
          { productCode: "TREA-182", atAuction: true, settledOn: "2026-11-13", maturesOn: "2027-05-14", yieldBps: 1312 },
        ];
  return async () => (sample === "error" ? Promise.reject(new Error("offline")) : buys);
}
