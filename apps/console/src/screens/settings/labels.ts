import type { Category } from "./api.ts";

/** In the order a client meets them, as in the client app's notification settings. */
export const CATEGORY_LABELS: Record<Category, string> = {
  money: "Money in and out",
  investments: "Investments",
  earnings: "Earnings",
  account: "Your account",
  security: "Security",
  support: "Help and complaints",
  news: "News from Finvest",
};
export const CATEGORIES = Object.keys(CATEGORY_LABELS) as Category[];

/** What each message tells the client, for staff. Falls back to the event name for one added later. */
const EVENT_LABELS: Record<string, string> = {
  payment_confirmed: "Payment received",
  payment_failed: "Payment failed",
  payout_processing: "Withdrawal being processed",
  payout_sent: "Withdrawal sent",
  payout_failed: "Withdrawal could not be sent",
  payout_declined: "Withdrawal declined after review",
  investment_started: "Investment started",
  investment_maturing: "Investment matures soon",
  investment_matured: "Investment matured",
  investment_withdrawal_requested: "Early withdrawal requested",
  investment_withdrawal_paid: "Early withdrawal sent",
  interest_earned: "Daily earnings",
  kyc_approved: "Identity check approved",
  kyc_retake_requested: "Identity photo needed again",
  kyc_rejected: "Identity check not approved",
  payout_destination_approved: "Payout account approved",
  payout_destination_refused: "Payout account not approved",
  statement_ready: "Statement ready",
  new_sign_in: "New sign-in",
  password_changed: "Password changed",
  second_step_changed: "Sign-in settings changed",
  support_reply: "Reply in the help chat",
  complaint_received: "Complaint received",
  complaint_closed: "Complaint closed",
};

export function eventLabel(event: string): string {
  return EVENT_LABELS[event] ?? event.replaceAll("_", " ");
}

const STAMP = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Africa/Kampala",
});

/** "12 Nov, 12:30", Kampala time. */
export function stamp(iso: string): string {
  return STAMP.format(new Date(iso));
}
