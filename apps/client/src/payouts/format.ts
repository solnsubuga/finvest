import { networkName } from "@/payments/format";

import { AccountStatus, Payout, PayoutAccount, PayoutStatus, ProofKind, RejectionReason } from "./types";

/** "MTN Mobile Money" or "Stanbic Bank": what the account is, without its number. */
export function accountProvider(account: Pick<PayoutAccount, "kind" | "network" | "bankName">): string {
  if (account.kind === "mobile_money") {
    return account.network ? `${networkName[account.network]} Mobile Money` : "Mobile money";
  }
  return account.bankName ?? "Bank";
}

/** "MTN +256 77• ••• 456", "Stanbic Bank •••• 4821". The number arrives masked. */
export function accountLabel(account: Pick<PayoutAccount, "kind" | "network" | "bankName" | "account">): string {
  const name = account.kind === "mobile_money" ? (account.network ? networkName[account.network] : "Mobile money") : (account.bankName ?? "Bank");
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
  network_name_unavailable:
    "The network has no name registered for this number, so we cannot check it is yours. Use a number registered in your name.",
};

/** Whether the client can send new proof after this refusal. A different name and suspected fraud are final. */
export function canProveAgain(reason: RejectionReason | null): boolean {
  return (
    reason !== null && reason !== "name_different" && reason !== "suspected_fraud" && reason !== "network_name_unavailable"
  );
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

/** A payout that came back is still "paid" by status, but the client needs to act on it. */
export const returnedPayoutCopy = {
  label: "Came back",
  tone: "warning" as Tone,
  detail:
    "The bank or network sent this money back to us, so it is safe with us. Choose an account and we will send it again.",
};

/** One that came back and was sent again: the new payout carries the money now. */
export const resentPayoutCopy = {
  label: "Sent again",
  tone: "neutral" as Tone,
  detail: "This payout came back to us, and you sent it again. The new payout has the money now.",
};

/** How a payout stands, as its pill and note show it. */
export function payoutCopy(payout: Pick<Payout, "status" | "returnedAt" | "resentAs">): { label: string; tone: Tone; detail: string } {
  if (payout.resentAs) return resentPayoutCopy;
  return payout.returnedAt ? returnedPayoutCopy : payoutStatusCopy[payout.status];
}
