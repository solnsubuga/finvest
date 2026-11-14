import { networkName } from "@/payments/format";

import { AccountStatus, PayoutAccount, PayoutStatus, ProofKind, RejectionReason } from "./types";

/**
 * The banks a client can be paid to, by the code the API stores. The backend checks only
 * the code's shape, so this list is where the codes are set. Commercial banks licensed by
 * the Bank of Uganda, by the name clients know them by.
 */
export const BANKS: readonly { code: string; name: string }[] = [
  { code: "absa", name: "Absa Bank" },
  { code: "bank_of_africa", name: "Bank of Africa" },
  { code: "bank_of_baroda", name: "Bank of Baroda" },
  { code: "centenary", name: "Centenary Bank" },
  { code: "citibank", name: "Citibank" },
  { code: "dfcu", name: "dfcu Bank" },
  { code: "diamond_trust", name: "Diamond Trust Bank" },
  { code: "ecobank", name: "Ecobank" },
  { code: "equity", name: "Equity Bank" },
  { code: "exim", name: "Exim Bank" },
  { code: "finance_trust", name: "Finance Trust Bank" },
  { code: "gtbank", name: "Guaranty Trust Bank" },
  { code: "housing_finance", name: "Housing Finance Bank" },
  { code: "i_and_m", name: "I&M Bank" },
  { code: "kcb", name: "KCB Bank" },
  { code: "ncba", name: "NCBA Bank" },
  { code: "opportunity", name: "Opportunity Bank" },
  { code: "pride", name: "Pride Bank" },
  { code: "stanbic", name: "Stanbic Bank" },
  { code: "standard_chartered", name: "Standard Chartered" },
  { code: "tropical", name: "Tropical Bank" },
  { code: "uba", name: "United Bank for Africa" },
];

export function bankName(code: string | null): string {
  return BANKS.find((bank) => bank.code === code)?.name ?? "Bank";
}

/** "MTN Mobile Money" or "Stanbic Bank": what the account is, without its number. */
export function accountProvider(account: Pick<PayoutAccount, "kind" | "network" | "bankCode">): string {
  if (account.kind === "mobile_money") {
    return account.network ? `${networkName[account.network]} Mobile Money` : "Mobile money";
  }
  return bankName(account.bankCode);
}

/** "MTN +256 77• ••• 456", "Stanbic Bank •••• 4821". The number arrives masked. */
export function accountLabel(account: Pick<PayoutAccount, "kind" | "network" | "bankCode" | "account">): string {
  const name = account.kind === "mobile_money" ? (account.network ? networkName[account.network] : "Mobile money") : bankName(account.bankCode);
  return `${name} ${account.account}`;
}

/** Whether the account can be paid at `nowMs`: approved and past its first day. */
export function isPayable(account: Pick<PayoutAccount, "status" | "payableFrom">, nowMs: number): boolean {
  return account.status === "approved" && account.payableFrom !== null && Date.parse(account.payableFrom) <= nowMs;
}

export type Tone = "lime" | "warning" | "danger" | "neutral";

export function accountStatusPill(account: Pick<PayoutAccount, "status" | "payableFrom" | "kind">, nowMs: number): { label: string; tone: Tone } {
  const pills: Record<AccountStatus, { label: string; tone: Tone }> = {
    awaiting_code: { label: "Enter the code", tone: "warning" },
    awaiting_proof: account.kind === "bank" ? { label: "Send proof", tone: "warning" } : { label: "Checking", tone: "neutral" },
    in_review: { label: "Checking", tone: "neutral" },
    approved: isPayable(account, nowMs) ? { label: "Ready", tone: "lime" } : { label: "Ready soon", tone: "neutral" },
    rejected: { label: "Not approved", tone: "danger" },
  };
  return pills[account.status];
}

export const proofKindCopy: Record<ProofKind, { title: string; detail: string }> = {
  bank_statement: { title: "Bank statement", detail: "Dated in the last three months, showing your name and the account number." },
  bank_letter: { title: "Letter from the bank", detail: "On the bank's letterhead, naming you as the account holder." },
  bank_card: { title: "Photo of the bank card", detail: "The side that shows your name." },
};

export const rejectionCopy: Record<RejectionReason, string> = {
  name_different:
    "The name on the account is not the name on your ID. We can only pay an account in your own name, so this account cannot be used.",
  proof_unreadable: "We could not read the proof you sent. Send a clearer copy.",
  proof_too_old: "The statement was more than three months old. Send a newer one.",
  proof_wrong_account: "The proof was for a different account number. Send proof for this account.",
  proof_wrong_document: "That document does not prove the account is yours. Send a statement, a bank letter or a photo of the card.",
  suspected_fraud: "This account cannot be used. Talk to us in Help if you think this is a mistake.",
};

/** Whether the client can send new proof after this refusal. A different name and suspected fraud are final. */
export function canProveAgain(reason: RejectionReason | null): boolean {
  return reason !== null && reason !== "name_different" && reason !== "suspected_fraud";
}

export const payoutStatusCopy: Record<PayoutStatus, { label: string; tone: Tone; detail: string }> = {
  in_review: {
    label: "Checking",
    tone: "neutral",
    detail: "We are checking this withdrawal before it is sent. We will tell you when it goes.",
  },
  processing: {
    label: "On its way",
    tone: "neutral",
    detail: "The money is on its way to your account. We will tell you when it arrives.",
  },
  paid: { label: "Paid", tone: "lime", detail: "The money reached your account." },
  failed: {
    label: "Not sent",
    tone: "danger",
    detail: "The money could not be sent, so it is safe with us. We will be in touch about it.",
  },
  cancelled: { label: "Cancelled", tone: "neutral", detail: "This payout was stopped before it was sent." },
};
