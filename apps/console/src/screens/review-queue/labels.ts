import type { AccountKind, Amount, Currency, Network, PayoutAccount, ProofKind, RejectionReason } from "./api.ts";

const MINOR_DIGITS: Record<Currency, number> = { UGX: 0, USD: 2 };
const SYMBOL: Record<Currency, string> = { UGX: "UGX ", USD: "$" };
const GROUPED = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

/** The exact amount: "UGX 12,500,000". A person approves it, so nothing is rounded. */
export function formatAmount(amount: Amount): string {
  const digits = MINOR_DIGITS[amount.currency];
  const unit = 10 ** digits;
  const whole = Math.trunc(Math.abs(amount.minor) / unit);
  const fraction = digits ? `.${String(Math.abs(amount.minor) % unit).padStart(digits, "0")}` : "";
  return `${amount.minor < 0 ? "-" : ""}${SYMBOL[amount.currency]}${GROUPED.format(whole)}${fraction}`;
}

const DAY = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const MOMENT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Africa/Kampala",
});

/** A calendar date: "Thu 12 Nov 2026". */
export function formatDay(day: string): string {
  return DAY.format(new Date(`${day}T00:00:00Z`));
}

/** A moment, in Kampala time: "13 Nov, 14:20". */
export function formatMoment(moment: string): string {
  return MOMENT.format(new Date(moment));
}

export function timeAgo(iso: string, now: Date): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

export const KIND_LABELS: Record<AccountKind, string> = { mobile_money: "Mobile money", bank: "Bank account" };
export const NETWORK_LABELS: Record<Network, string> = { mtn: "MTN Mobile Money", airtel: "Airtel Money" };

export const PROOF_LABELS: Record<ProofKind, string> = {
  bank_statement: "Bank statement",
  bank_letter: "Letter from the bank",
  bank_card: "Photo of the bank card",
  network_registered_name: "Name the network holds",
};

/** The reasons a person can give for refusing proof, worded as staff would say them. */
export const REJECTION_LABELS: Record<RejectionReason, string> = {
  name_different: "The name is not the client's",
  proof_unreadable: "The proof cannot be read",
  proof_too_old: "The statement is too old",
  proof_wrong_account: "The proof is for another account",
  proof_wrong_document: "Not a document we accept",
  suspected_fraud: "Suspected fraud (final)",
};

/**
 * A bank's code as a name: `stanbic` is "Stanbic", `centenary_bank` is "Centenary Bank", and a short
 * code such as `dfcu` is read as letters, "DFCU". The console has no list of banks yet, so the code
 * is all it has.
 */
export function bankName(code: string): string {
  if (!code.includes("_") && code.length <= 4) return code.toUpperCase();
  return code
    .split("_")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/** Where the money goes, without the number: "MTN Mobile Money" or "Stanbic". */
export function providerName(account: Pick<PayoutAccount, "kind" | "network" | "bankCode">): string {
  if (account.kind === "mobile_money") return account.network ? NETWORK_LABELS[account.network] : "Mobile money";
  return account.bankCode ? bankName(account.bankCode) : "Bank account";
}

/** "MTN Mobile Money ••••4821" or "Stanbic ••••0193". */
export function accountLine(account: Pick<PayoutAccount, "kind" | "network" | "bankCode" | "account">): string {
  return `${providerName(account)} ${account.account}`;
}
