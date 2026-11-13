import { FailureReason, MismatchReason, Money, Network } from "./types";

/** Digits after the decimal point in each currency's smallest unit: whole shillings, US cents. */
export const MINOR_DIGITS: Record<Money["currency"], number> = { UGX: 0, USD: 2 };

/**
 * "UGX 1,000,000" or "USD 500.00". The point is placed by moving digits of the
 * integer, never by dividing, so no amount passes through a fraction.
 */
export function formatMoney(money: Money): string {
  if (!Number.isSafeInteger(money.minor)) {
    throw new Error("An amount must be a whole number of the smallest unit");
  }
  const places = MINOR_DIGITS[money.currency];
  const digits = String(Math.abs(money.minor)).padStart(places + 1, "0");
  const whole = digits.slice(0, digits.length - places).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fraction = places > 0 ? `.${digits.slice(digits.length - places)}` : "";
  return `${money.currency} ${money.minor < 0 ? "-" : ""}${whole}${fraction}`;
}

export const networkName: Record<Network, string> = { mtn: "MTN", airtel: "Airtel" };
export const networkProduct: Record<Network, string> = {
  mtn: "MTN Mobile Money",
  airtel: "Airtel Money",
};
/** The code a client dials to find a prompt that did not pop up. */
export const pendingApprovalsCode: Record<Network, string> = { mtn: "*165#", airtel: "*185#" };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Uganda keeps UTC+3 all year, so the offset is fixed. */
const KAMPALA_OFFSET_MS = 3 * 60 * 60 * 1000;

function kampalaParts(iso: string) {
  const shifted = new Date(new Date(iso).getTime() + KAMPALA_OFFSET_MS);
  const two = (value: number) => String(value).padStart(2, "0");
  return {
    day: shifted.getUTCDate(),
    month: MONTHS[shifted.getUTCMonth()],
    year: shifted.getUTCFullYear(),
    time: `${two(shifted.getUTCHours())}:${two(shifted.getUTCMinutes())}`,
  };
}

/** "15:31", Kampala time, whatever the phone's own time zone is. */
export function formatTime(iso: string): string {
  return kampalaParts(iso).time;
}

/** "13 Nov 2026, 15:32", Kampala time. */
export function formatDateTime(iso: string): string {
  const parts = kampalaParts(iso);
  return `${parts.day} ${parts.month} ${parts.year}, ${parts.time}`;
}

type FailureCopy = {
  /** "MTN says there was not enough money on +256 772 123 456." */
  sentence: (network: string, payer: string) => string;
  /** The short version for the details list. */
  label: string;
};

export const failureCopy: Record<FailureReason, FailureCopy> = {
  declined: {
    sentence: (network, payer) => `${network} says the payment was declined on ${payer}.`,
    label: "Declined",
  },
  insufficient_funds: {
    sentence: (network, payer) => `${network} says there was not enough money on ${payer}.`,
    label: "Insufficient balance",
  },
  approval_timed_out: {
    sentence: (network, payer) =>
      `${network} says the prompt on ${payer} was not approved in time.`,
    label: "Not approved in time",
  },
  account_not_found: {
    sentence: (network, payer) =>
      `${network} says ${payer} is not registered for mobile money with them.`,
    label: "Number not registered",
  },
  limit_exceeded: {
    sentence: (network, payer) =>
      `${network} says this payment is over the limit for ${payer}.`,
    label: "Over the limit",
  },
  other: {
    sentence: (network) => `${network} could not complete the payment.`,
    label: "Could not complete",
  },
};

/** "214 KB" or "1.4 MB". */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "13 Nov 2026" from "2026-11-13". */
export function formatDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return `${date} ${MONTHS[(month ?? 1) - 1]} ${year}`;
}

/** Why a receipt was not matched, in plain words the client can act on. */
export const mismatchCopy: Record<MismatchReason, string> = {
  amount_differs: "The amount on the receipt is different from the amount we received.",
  not_on_statement: "We could not find this transfer on our bank statement.",
  unreadable: "We could not read the receipt. Upload a clearer photo or the PDF from your bank.",
  name_differs:
    "The transfer came from an account in a different name. We only accept money from an account in your own name.",
  other: "We could not match this receipt to a transfer.",
};
