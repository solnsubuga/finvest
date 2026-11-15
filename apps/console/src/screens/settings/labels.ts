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
  payment_reversed: "Payment taken back",
  payout_processing: "Withdrawal being processed",
  payout_sent: "Withdrawal sent",
  payout_failed: "Withdrawal could not be sent",
  payout_declined: "Withdrawal declined after review",
  payout_returned: "Withdrawal came back",
  investment_started: "Investment started",
  investment_maturing: "Investment matures soon",
  investment_matured: "Investment matured",
  investment_withdrawal_requested: "Early withdrawal requested",
  investment_withdrawal_paid: "Early withdrawal sent",
  interest_earned: "Daily earnings",
  goal_top_up_due: "Goal saving due soon",
  goal_reached: "Goal reached",
  goal_payday: "Payday nudge to save",
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
  investment_booked: "Investment booked for the client to pay",
  investment_sent: "Investment sent to another client",
  investment_transfer_held: "Transfer to another client held for checks",
  investment_transfer_failed: "Transfer to another client not made",
  goal_interest_saved: "Interest saved to a goal",
  goal_interest_not_saved: "Interest kept invested, not saved to the goal",
  autoinvest_run_failed: "Automatic investment not made",
  autoinvest_run_missed: "Automatic investment missed",
  autoinvest_run_retrying: "Automatic investment not paid yet",
  organisation_invited: "Asked to sign for a company or group account",
  organisation_approved: "Company or group account open",
  organisation_returned: "Company or group application sent back",
  organisation_rejected: "Company or group application not approved",
  announcement: "Announcement",
};

/** The label for an event, or for one added later, its name as a sentence: "Investment booked". */
export function eventLabel(event: string): string {
  const label = EVENT_LABELS[event];
  if (label) return label;
  const words = event.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
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
