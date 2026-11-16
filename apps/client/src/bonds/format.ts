import { formatDay } from "@/investing/format";

import { Bond, BondHolding, HoldingStatus, SaleStatus } from "./types";

/**
 * "98.50" for 985000: a price per 100 of face value, held to four decimal places as an integer.
 * Works on the integer, so no price passes through a fraction. At least two decimals, at most four.
 */
export function formatPrice(price: number): string {
  if (!Number.isSafeInteger(price) || price < 0) {
    throw new Error("A price must be a whole number of ten-thousandths");
  }
  const whole = Math.floor(price / 10_000);
  const fraction = String(price % 10_000)
    .padStart(4, "0")
    .replace(/0+$/, "")
    .padEnd(2, "0");
  return `${whole}.${fraction}`;
}

/** "98.50 per 100", or that there is no price yet. */
export function priceLabel(price: number | null): string {
  return price === null ? "No price yet" : `${formatPrice(price)} per 100`;
}

/** "Matures 14 May 2037" */
export function maturesLabel(bond: Pick<Bond, "maturityDate">): string {
  return `Matures ${formatDay(bond.maturityDate)}`;
}

/** "Every six months, next on 14 May 2027", or that only the one at maturity is left. */
export function couponsLabel(bond: Pick<Bond, "nextCouponDate" | "couponsLeft">): string {
  if (bond.nextCouponDate === null) return "All paid";
  return bond.couponsLeft <= 1
    ? `One left, paid with the face value on ${formatDay(bond.nextCouponDate)}`
    : `Every six months, next on ${formatDay(bond.nextCouponDate)}`;
}

/** Why a bond that is shown cannot be bought, in a few words, or null when it can. */
export function notBuyableLabel(bond: Bond): string | null {
  if (bond.canBuy) return null;
  if (bond.status === "paused") return "Paused for new buyers";
  if (bond.price === null) return "No price today";
  return "Too close to maturity to buy";
}

export const holdingStatusLabel: Record<HoldingStatus, string> = {
  unpaid: "Waiting for payment",
  active: "Paying coupons",
  matured: "Repaid at maturity",
  sold: "Sold",
  cancelled: "Cancelled",
};

/** On the holding's card: what to do, or when it is next paid. */
export function holdingLine(holding: BondHolding): string {
  if (holding.status === "unpaid") return "Pay to buy it";
  if (holding.status === "active" && holding.nextCouponDate) return `Next coupon ${formatDay(holding.nextCouponDate)}`;
  return `${holding.status === "active" ? "Matures" : "Matured"} ${formatDay(holding.maturityDate)}`;
}

/** Where a sale stands, as the client hears it. */
export const saleStatusLabel: Record<SaleStatus, string> = {
  requested: "Asked to sell. Finvest's dealing desk is finding a buyer.",
  priced: "A buyer is found. Finvest is checking the price before it settles.",
  approved: "Sold. The money is being paid to you.",
  sold: "Sold",
  cancelled: "You cancelled this ask to sell",
  closed: "The bond matured before it was sold, so it was repaid instead",
};

/** Why money waits for an account, in words a client can act on. */
export function heldLabel(heldBecause: string | null): string {
  if (heldBecause === "no_destination") return "Waiting for you to choose an account";
  if (heldBecause === "destination_in_safety_wait") return "Waiting for a new account's first day to pass";
  return "Waiting for an account that can be paid";
}
