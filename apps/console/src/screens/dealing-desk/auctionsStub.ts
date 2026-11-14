import {
  AuctionError,
  type Auction,
  type AuctionCalendar,
  type AuctionStep,
  type AuctionsApi,
  type Cancellation,
  type ChangeStatus,
} from "./auctions.ts";

/**
 * A sample auction calendar for `pnpm dev` and previews. Not the Bank of Uganda's real dates and
 * not connected to Finvest. Its standing dates agree with the desk's sample (stub.ts). Commands
 * change only this browser tab's copy, with the same two-person rules as the backend. The
 * colleague is Grace, so the signed-in person can approve what she proposed.
 */

export const SAMPLE_COLLEAGUE = { id: "-Nstaff00000000grace", name: "Grace" };

interface Stored {
  id: string;
  productCode: string;
  auctionDate: string;
  status: ChangeStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
  cancellations: StoredCancellation[];
}

type StoredCancellation = Omit<Cancellation, "allowed">;

const BILLS = new Set(["TREA-91", "TREA-182"]);
const GRACE = SAMPLE_COLLEAGUE.name;

function sample(me: string): Stored[] {
  const date = (
    id: string,
    productCode: string,
    auctionDate: string,
    extra: Partial<Stored> = {},
  ): Stored => ({
    id,
    productCode,
    auctionDate,
    status: "approved",
    proposedBy: GRACE,
    proposedAt: "2026-10-20T07:12:00Z",
    decidedBy: me,
    decidedAt: "2026-10-20T08:30:00Z",
    decisionNote: null,
    cancellations: [],
    ...extra,
  });
  return [
    date("-Nauction0000000001", "TREA-91", "2026-11-04"),
    date("-Nauction0000000002", "TREA-182", "2026-11-04"),
    date("-Nauction0000000003", "TREA-91", "2026-11-16", {
      cancellations: [
        {
          id: "-Nauctioncancel00001",
          reason: "The Bank moved it to Wednesday 18 November",
          status: "approved",
          proposedBy: GRACE,
          proposedAt: "2026-11-10T09:02:00Z",
          decidedBy: me,
          decidedAt: "2026-11-10T09:40:00Z",
          decisionNote: null,
        },
      ],
    }),
    date("-Nauction0000000004", "TREA-91", "2026-11-18", {
      proposedBy: me,
      proposedAt: "2026-11-10T09:03:00Z",
      decidedBy: GRACE,
    }),
    date("-Nauction0000000005", "TREA-182", "2026-11-18"),
    date("-Nauction0000000006", "TREA-91", "2026-12-02", {
      cancellations: [
        {
          id: "-Nauctioncancel00002",
          reason: "The Bank moved it to Thursday 3 December",
          status: "proposed",
          proposedBy: GRACE,
          proposedAt: "2026-11-13T14:20:00Z",
          decidedBy: null,
          decidedAt: null,
          decisionNote: null,
        },
      ],
    }),
    date("-Nauction0000000007", "TREA-182", "2026-12-02"),
    date("-Nauction0000000008", "TREA-91", "2026-12-16", {
      status: "proposed",
      proposedAt: "2026-11-14T06:50:00Z",
      decidedBy: null,
      decidedAt: null,
    }),
  ];
}

export function createStubAuctionsApi(
  me: { name: string },
  kind: "sample" | "empty" = "sample",
  today = () => new Date().toISOString().slice(0, 10),
  delayMs = 150,
): AuctionsApi {
  let dates = kind === "empty" ? [] : sample(me.name);
  let created = 0;
  const replies = new Map<string, AuctionCalendar>();
  const nextId = (prefix: string) =>
    `${prefix}${String((created += 1)).padStart(7, "0")}`;

  const live = (d: Stored) =>
    d.cancellations.find(
      (c) => c.status === "proposed" || c.status === "approved",
    );
  function state(d: Stored): Auction["state"] {
    if (d.status === "proposed") return "waiting";
    if (d.status !== "approved") return d.status;
    const c = live(d);
    return c?.status === "approved"
      ? "cancelled"
      : c
        ? "cancelling"
        : "standing";
  }
  function steps(
    proposedBy: string,
    waiting: boolean,
    auctionDate: string,
    isDate: boolean,
  ): AuctionStep[] {
    if (!waiting) return [];
    if (proposedBy === me.name) return ["withdraw"];
    return isDate && auctionDate < today()
      ? ["decline"]
      : ["approve", "decline"];
  }
  function view(d: Stored): Auction {
    const c = live(d) ?? d.cancellations.at(-1) ?? null;
    const s = state(d);
    return {
      id: d.id,
      productCode: d.productCode,
      auctionDate: d.auctionDate,
      state: s,
      proposedBy: d.proposedBy,
      proposedAt: d.proposedAt,
      decidedBy: d.decidedBy,
      decidedAt: d.decidedAt,
      decisionNote: d.decisionNote,
      cancellation: c
        ? {
            ...c,
            allowed: steps(
              c.proposedBy,
              c.status === "proposed",
              d.auctionDate,
              false,
            ),
          }
        : null,
      allowed:
        s === "standing"
          ? d.auctionDate >= today()
            ? ["cancel"]
            : []
          : steps(d.proposedBy, s === "waiting", d.auctionDate, true),
    };
  }
  const calendar = (): AuctionCalendar => ({
    auctions: [...dates]
      .sort(
        (a, b) =>
          a.auctionDate.localeCompare(b.auctionDate) ||
          a.productCode.localeCompare(b.productCode) ||
          a.proposedAt.localeCompare(b.proposedAt),
      )
      .map(view),
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
  const now = () => new Date().toISOString();
  function find(changeId: string): {
    date: Stored;
    cancellation: StoredCancellation | null;
  } {
    for (const d of dates) {
      if (d.id === changeId) return { date: d, cancellation: null };
      const c = d.cancellations.find((x) => x.id === changeId);
      if (c) return { date: d, cancellation: c };
    }
    throw new AuctionError("not_found");
  }
  function decide(
    changeId: string,
    step: "approve" | "decline" | "withdraw",
    note = "",
  ) {
    const { date, cancellation } = find(changeId);
    const change = cancellation ?? date;
    if (change.status !== "proposed") throw new AuctionError("conflict");
    const own = change.proposedBy === me.name;
    if (own !== (step === "withdraw")) throw new AuctionError("forbidden");
    if (step === "decline" && !note.trim())
      throw new AuctionError("note_required");
    if (step === "approve" && !cancellation && date.auctionDate < today())
      throw new AuctionError("auction_date_passed");
    const status: ChangeStatus =
      step === "approve"
        ? "approved"
        : step === "decline"
          ? "declined"
          : "withdrawn";
    Object.assign(change, {
      status,
      decidedBy: me.name,
      decidedAt: now(),
      decisionNote: note.trim() || null,
    });
    dates = [...dates];
  }

  return {
    getCalendar: () => later(calendar),
    propose: (key, productCode, auctionDate) =>
      once(key, () => {
        if (!BILLS.has(productCode))
          throw new AuctionError("not_a_treasury_bill");
        if (auctionDate < today())
          throw new AuctionError("auction_date_passed");
        if (
          dates.some(
            (d) =>
              d.productCode === productCode &&
              d.auctionDate === auctionDate &&
              ["waiting", "standing", "cancelling"].includes(state(d)),
          )
        ) {
          throw new AuctionError("auction_already_entered");
        }
        dates = [
          ...dates,
          {
            id: nextId("-Nauctionnew"),
            productCode,
            auctionDate,
            status: "proposed",
            proposedBy: me.name,
            proposedAt: now(),
            decidedBy: null,
            decidedAt: null,
            decisionNote: null,
            cancellations: [],
          },
        ];
      }),
    proposeCancel: (key, auctionId, reason) =>
      once(key, () => {
        const { date } = find(auctionId);
        if (state(date) === "cancelling") throw new AuctionError("conflict");
        if (state(date) !== "standing") throw new AuctionError("conflict");
        if (!reason.trim()) throw new AuctionError("reason_required");
        date.cancellations = [
          ...date.cancellations,
          {
            id: nextId("-Nauctioncancelnew"),
            reason: reason.trim(),
            status: "proposed",
            proposedBy: me.name,
            proposedAt: now(),
            decidedBy: null,
            decidedAt: null,
            decisionNote: null,
          },
        ];
        dates = [...dates];
      }),
    approve: (key, id) => once(key, () => decide(id, "approve")),
    decline: (key, id, note) => once(key, () => decide(id, "decline", note)),
    withdraw: (key, id) => once(key, () => decide(id, "withdraw")),
  };
}
