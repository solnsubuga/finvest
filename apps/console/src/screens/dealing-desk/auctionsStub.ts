import {
  AuctionError,
  type Auction,
  type AuctionCalendar,
  type AuctionsApi,
} from "./auctions.ts";

/**
 * A sample auction calendar for `pnpm dev` and previews. Not the Bank of Uganda's real dates and
 * not connected to Finvest. Its next dates agree with the desk's sample (stub.ts). Commands change
 * only this browser tab's copy.
 */

export const SAMPLE_COLLEAGUE = { id: "-Nstaff00000000grace", name: "Grace" };

function sample(): Auction[] {
  const entered = (
    id: string,
    productCode: string,
    auctionDate: string,
    addedAt: string,
  ): Auction => ({
    id,
    productCode,
    auctionDate,
    addedBy: SAMPLE_COLLEAGUE.name,
    addedAt,
    cancelledBy: null,
    cancelledAt: null,
    cancelReason: null,
  });
  return [
    entered(
      "-Nauction0000000001",
      "TREA-91",
      "2026-11-04",
      "2026-10-20T07:12:00Z",
    ),
    entered(
      "-Nauction0000000002",
      "TREA-182",
      "2026-11-04",
      "2026-10-20T07:13:00Z",
    ),
    {
      ...entered(
        "-Nauction0000000003",
        "TREA-91",
        "2026-11-16",
        "2026-10-20T07:14:00Z",
      ),
      cancelledBy: SAMPLE_COLLEAGUE.name,
      cancelledAt: "2026-11-10T09:02:00Z",
      cancelReason: "The Bank moved it to Wednesday 18 November",
    },
    entered(
      "-Nauction0000000004",
      "TREA-91",
      "2026-11-18",
      "2026-11-10T09:03:00Z",
    ),
    entered(
      "-Nauction0000000005",
      "TREA-182",
      "2026-11-18",
      "2026-10-20T07:15:00Z",
    ),
    entered(
      "-Nauction0000000006",
      "TREA-91",
      "2026-12-02",
      "2026-10-20T07:16:00Z",
    ),
    entered(
      "-Nauction0000000007",
      "TREA-182",
      "2026-12-02",
      "2026-10-20T07:17:00Z",
    ),
  ];
}

const BILLS = new Set(["TREA-91", "TREA-182"]);

export function createStubAuctionsApi(
  me: { name: string },
  kind: "sample" | "empty" = "sample",
  today = () => new Date().toISOString().slice(0, 10),
  delayMs = 150,
): AuctionsApi {
  let auctions = kind === "empty" ? [] : sample();
  let created = 0;
  const replies = new Map<string, AuctionCalendar>();

  const calendar = (): AuctionCalendar => ({
    auctions: [...auctions].sort(
      (a, b) =>
        a.auctionDate.localeCompare(b.auctionDate) ||
        a.productCode.localeCompare(b.productCode) ||
        a.addedAt.localeCompare(b.addedAt),
    ),
    mayChange: true,
  });
  const later = <T>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) => {
      setTimeout(() => {
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new Error(String(error)));
        }
      }, delayMs);
    });
  function once(key: string, command: () => void): Promise<AuctionCalendar> {
    return later(() => {
      const replied = replies.get(key);
      if (replied) return replied;
      command();
      const reply = calendar();
      replies.set(key, reply);
      return reply;
    });
  }

  return {
    getCalendar: () => later(calendar),
    add: (key, productCode, auctionDate) =>
      once(key, () => {
        if (!BILLS.has(productCode))
          throw new AuctionError("not_a_treasury_bill");
        if (auctionDate < today())
          throw new AuctionError("auction_date_passed");
        if (
          auctions.some(
            (a) =>
              a.productCode === productCode &&
              a.auctionDate === auctionDate &&
              !a.cancelledBy,
          )
        ) {
          throw new AuctionError("auction_already_entered");
        }
        created += 1;
        auctions = [
          ...auctions,
          {
            id: `-Nauctionnew${String(created).padStart(7, "0")}`,
            productCode,
            auctionDate,
            addedBy: me.name,
            addedAt: new Date().toISOString(),
            cancelledBy: null,
            cancelledAt: null,
            cancelReason: null,
          },
        ];
      }),
    cancel: (key, auctionId, reason) =>
      once(key, () => {
        const found = auctions.find((a) => a.id === auctionId);
        if (!found) throw new AuctionError("not_found");
        if (found.cancelledBy) throw new AuctionError("conflict");
        if (!reason.trim()) throw new AuctionError("reason_required");
        auctions = auctions.map((a) =>
          a.id === auctionId
            ? {
                ...a,
                cancelledBy: me.name,
                cancelledAt: new Date().toISOString(),
                cancelReason: reason.trim(),
              }
            : a,
        );
      }),
  };
}
