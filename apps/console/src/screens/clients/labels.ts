import type { Account, ClientStatus, Currency, Destination, Lock, Owed } from "./api.ts";

export const STATUS_LABELS: Record<ClientStatus, string> = {
  unclaimed: "Not signed up yet",
  active: "Active",
  locked: "Locked",
  closed: "Closed",
};

const OWED_LABELS: Record<string, string> = {
  "client.awaiting_start": "Paid in, waiting to start",
  "client.holding": "Invested",
  "client.interest_accrued": "Interest earned so far",
  "client.payout_pending": "Payout waiting to be sent",
  "client.payout_in_flight": "Payout on its way",
};

const DESTINATION_STATUS: Record<string, string> = {
  awaiting_code: "Waiting for the SMS code",
  awaiting_proof: "Waiting for proof",
  in_review: "Proof waiting for review",
  approved: "Approved",
  rejected: "Proof refused",
  removed: "Removed",
};

const NETWORKS: Record<string, string> = { mtn: "MTN Mobile Money", airtel: "Airtel Money" };

/** `in_review` → "In review", for statuses another module owns and may add to. */
export function humanize(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function owedLabel(item: Owed): string {
  return OWED_LABELS[item.accountCode] ?? item.accountCode;
}

export function destinationLabel(item: Destination): string {
  if (item.kind === "mobile_money") return item.network ? (NETWORKS[item.network] ?? humanize(item.network)) : "Mobile money";
  return item.bankCode ? `Bank (${item.bankCode.toUpperCase()})` : "Bank";
}

export function destinationStatus(item: Destination): string {
  return DESTINATION_STATUS[item.status] ?? humanize(item.status);
}

const MINOR_DIGITS: Record<Currency, number> = { UGX: 0, USD: 2 };
const SYMBOL: Record<Currency, string> = { UGX: "UGX ", USD: "$" };
const GROUPED = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

/** The exact amount, from whole minor units: "UGX 2,500,000". Nothing is rounded. */
export function formatAmount(minor: number, currency: Currency): string {
  const digits = MINOR_DIGITS[currency];
  const unit = 10 ** digits;
  const whole = Math.trunc(Math.abs(minor) / unit);
  const fraction = digits ? `.${String(Math.abs(minor) % unit).padStart(digits, "0")}` : "";
  return `${minor < 0 ? "-" : ""}${SYMBOL[currency]}${GROUPED.format(whole)}${fraction}`;
}

/** Totals per currency, summed in integers. */
export function totals(owed: readonly Owed[]): { currency: Currency; minor: number }[] {
  const sums = new Map<Currency, number>();
  for (const item of owed) sums.set(item.currency, (sums.get(item.currency) ?? 0) + item.minor);
  return [...sums].map(([currency, minor]) => ({ currency, minor }));
}

const TIME = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Africa/Kampala" });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

export function formatTime(iso: string): string {
  return TIME.format(new Date(iso));
}

export function formatDay(day: string): string {
  return DAY.format(new Date(`${day}T00:00:00Z`));
}

/** "1004 382 917", grouped as the Identity checks screen shows it. */
export function formatAccountNumber(number: string): string {
  return `${number.slice(0, 4)} ${number.slice(4, 7)} ${number.slice(7)}`;
}

export function secondSteps(account: Account): string {
  if (account.hasPasskey && account.hasAuthenticator) return "Passkey and authenticator app";
  if (account.hasPasskey) return "Passkey";
  if (account.hasAuthenticator) return "Authenticator app";
  return "Not set up";
}

export const LOCK_STATUS: Record<Lock["status"], string> = {
  proposed: "Waiting",
  approved: "Approved",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};
