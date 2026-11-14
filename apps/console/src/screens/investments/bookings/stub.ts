import type { Amount } from "../../products/api.ts";
import { BookingsError, type BookableProduct, type Booking, type BookingsApi, type NewBooking } from "./api.ts";

/**
 * Sample bookings for `pnpm dev` and previews. Not real clients or money. It keeps the
 * backend's rules a screen can show: the booker never decides, a decided booking never
 * changes, and the amount meets the product's minimum. Approving one saves it straight away,
 * as the worker would a moment later.
 */

const ugx = (minor: number): Amount => ({ minor, currency: "UGX" });
const OTHER = "sample-staff-grace";

const PRODUCTS: readonly BookableProduct[] = [
  { code: "TREA-91", name: "91-day Treasury bill", currency: "UGX", termDays: 91, minimum: ugx(100_000) },
  { code: "TREA-182", name: "182-day Treasury bill", currency: "UGX", termDays: 182, minimum: ugx(100_000) },
  { code: "TREA-364", name: "364-day Treasury bill", currency: "UGX", termDays: 364, minimum: ugx(100_000) },
];

function samples(me: string): Booking[] {
  const base = { decidedBy: null, decidedAt: null, decisionNote: null, investmentId: null, failureCode: null, finishedAt: null };
  return [
    {
      ...base,
      id: "-BookSample000000001",
      clientId: "sample-client-joan",
      productCode: "TREA-182",
      productName: "182-day Treasury bill",
      amount: ugx(3_000_000),
      atMaturity: "pay_interest",
      reason: "Joan called the office and asked for a 182-day bill, interest paid to her each term.",
      status: "proposed",
      proposedBy: OTHER,
      proposedAt: "2026-11-14T08:20:00Z",
    },
    {
      ...base,
      id: "-BookSample000000002",
      clientId: "sample-client-peter",
      productCode: "TREA-91",
      productName: "91-day Treasury bill",
      amount: ugx(500_000),
      atMaturity: "roll_over",
      reason: "Asked in the help chat to put 500,000 into the 91-day bill.",
      status: "done",
      proposedBy: me,
      proposedAt: "2026-11-13T10:05:00Z",
      decidedBy: OTHER,
      decidedAt: "2026-11-13T10:40:00Z",
      investmentId: "-InvSample00000000P2",
      finishedAt: "2026-11-13T10:40:03Z",
    },
  ];
}

export function createStubBookingsApi(me: string, sample: "default" | "empty" = "default", now = () => new Date().toISOString()): BookingsApi {
  let items = sample === "empty" ? [] : samples(me);
  const done = new Map<string, Booking>();
  const find = (id: string) => {
    const found = items.find((item) => item.id === id);
    if (!found) throw new BookingsError("not_found");
    return found;
  };
  const replace = (next: Booking) => {
    items = items.map((item) => (item.id === next.id ? next : item));
    return next;
  };
  // A key used again returns what it did the first time, as the API does.
  const once = (key: string, run: () => Booking) => {
    const seen = done.get(key);
    if (seen) return Promise.resolve(seen);
    try {
      const result = run();
      done.set(key, result);
      return Promise.resolve(result);
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new BookingsError("unavailable"));
    }
  };
  const decide = (id: string, step: (item: Booking) => Booking) => {
    const item = find(id);
    if (item.status !== "proposed") throw new BookingsError("conflict");
    return replace(step(item));
  };

  return {
    list: () => Promise.resolve([...items].sort((a, b) => b.proposedAt.localeCompare(a.proposedAt))),
    products: () => Promise.resolve(PRODUCTS),
    propose: (key, input: NewBooking) =>
      once(key, () => {
        const product = PRODUCTS.find((p) => p.code === input.productCode);
        if (!product || input.clientId === "sample-client-david") throw new BookingsError("not_found");
        if (input.amount.minor < product.minimum.minor) {
          throw new BookingsError("invalid", [{ field: "amount", code: "below_minimum" }]);
        }
        const created: Booking = {
          id: `-BookSample${String(items.length + 1).padStart(9, "0")}`,
          clientId: input.clientId,
          productCode: product.code,
          productName: product.name,
          amount: input.amount,
          atMaturity: product.termDays === null ? null : (input.atMaturity ?? "roll_over"),
          reason: input.reason.trim(),
          status: "proposed",
          proposedBy: me,
          proposedAt: now(),
          decidedBy: null,
          decidedAt: null,
          decisionNote: null,
          investmentId: null,
          failureCode: null,
          finishedAt: null,
        };
        items = [created, ...items];
        return created;
      }),
    approve: (key, id) =>
      once(key, () =>
        decide(id, (item) => {
          if (item.proposedBy === me) throw new BookingsError("same_person");
          const at = now();
          return { ...item, status: "done", decidedBy: me, decidedAt: at, finishedAt: at, investmentId: "-InvSample00000000P9" };
        }),
      ),
    sendBack: (key, id, note) =>
      once(key, () =>
        decide(id, (item) => {
          if (item.proposedBy === me) throw new BookingsError("same_person");
          return { ...item, status: "sent_back", decidedBy: me, decidedAt: now(), decisionNote: note.trim() };
        }),
      ),
    withdraw: (key, id) =>
      once(key, () =>
        decide(id, (item) => {
          if (item.proposedBy !== me) throw new BookingsError("forbidden");
          return { ...item, status: "withdrawn", decidedBy: me, decidedAt: now() };
        }),
      ),
  };
}
