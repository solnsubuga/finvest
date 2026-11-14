import { formatAmount } from "./format";
import { Transfer } from "./types";

/** What a transfer's state means to the person reading it. */
export function transferStatusLabel(transfer: Transfer): string {
  switch (transfer.status) {
    case "done":
      return transfer.direction === "sent" ? "Sent" : "Received";
    case "waiting":
      return "Sending";
    case "held":
    case "release_proposed":
      return "Being checked";
    case "failed":
      return "Not sent";
    case "declined":
      return "Turned down";
    case "cancelled":
      return "Cancelled";
  }
}

/** "To Brian K." or "From Amara N.". */
export function transferTitle(transfer: Transfer): string {
  return `${transfer.direction === "sent" ? "To" : "From"} ${transfer.otherName}`;
}

/** The amount, or "Everything" when the whole investment was sent and is not done yet. */
export function transferAmount(transfer: Transfer): string {
  return transfer.whole && transfer.status !== "done" ? `Everything, ${formatAmount(transfer.amount)} so far` : formatAmount(transfer.amount);
}

/** Why a transfer could not be made, in words. The codes come from the backend's refusals. */
export function failureReason(code: string | null): string {
  switch (code) {
    case "matures_today":
      return "The investment reached its maturity date first, so it was rolled over or paid out instead.";
    case "withdrawal_requested":
      return "You asked to withdraw the investment early first.";
    case "investment_not_running":
      return "The investment had already ended.";
    default:
      return "The investment changed before it could be sent. Nothing moved.";
  }
}

export const OPEN_TRANSFER = ["waiting", "held", "release_proposed"] as const;

export function canCancel(transfer: Transfer): boolean {
  return transfer.direction === "sent" && (transfer.status === "held" || transfer.status === "release_proposed");
}
