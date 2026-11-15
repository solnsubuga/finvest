import { describe, expect, it } from "vitest";
import type { Http } from "@/api/http.ts";
import { createHttpLastBuys, latestBuy } from "./lastBuys.ts";

describe("last buys", () => {
  it("reads the route and maps each buy", async () => {
    const asked: string[] = [];
    const http: Http = {
      request: async <T,>(method: string, path: string) => {
        asked.push(`${method} ${path}`);
        return {
          buys: [
            {
              product_code: "TREA-182",
              at_auction: true,
              face_minor: 10_000_000,
              paid_minor: 9_400_000,
              currency: "UGX",
              settled_on: "2026-11-13",
              matures_on: "2027-05-14",
              yearly_yield_bps: 1312,
            },
          ],
        } as T;
      },
      download: async () => new Blob(),
    };
    expect(await createHttpLastBuys(http)()).toEqual([
      { productCode: "TREA-182", atAuction: true, settledOn: "2026-11-13", maturesOn: "2027-05-14", yieldBps: 1312 },
    ]);
    expect(asked).toEqual(["GET /staff/dealing/last-buys"]);
  });

  it("picks the latest settled day", () => {
    const at = (productCode: string, settledOn: string) => ({ productCode, atAuction: true, settledOn, maturesOn: "2027-01-01", yieldBps: 1000 });
    expect(latestBuy([at("A", "2026-11-01"), at("B", "2026-11-09"), at("C", "2026-11-03")])?.productCode).toBe("B");
    expect(latestBuy([])).toBeUndefined();
  });
});
