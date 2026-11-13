import { Category, Channel, ComplaintCategory, ComplaintOutcome, ComplaintStatus } from "./types";

/** The kinds of alert, in the order the settings screen lists them. */
export const categories: { category: Category; title: string; about: string }[] = [
  { category: "money", title: "Money in and out", about: "Payments received and withdrawals sent." },
  { category: "investments", title: "Investments", about: "When one starts, is about to mature or matures." },
  { category: "earnings", title: "Daily earnings", about: "What your investments earned each day." },
  { category: "account", title: "Account", about: "Your identity check, payout accounts and statements." },
  { category: "security", title: "Security", about: "New sign-ins and changes to how you sign in." },
  { category: "support", title: "Help", about: "Replies from our team and news on a complaint." },
  { category: "news", title: "News from Finvest", about: "New products and service notices." },
];

export const channelNames: Record<Channel, string> = { push: "Push", sms: "SMS", email: "Email" };

export const channels: Channel[] = ["push", "sms", "email"];

export const complaintCategories: { category: ComplaintCategory; title: string }[] = [
  { category: "payments", title: "A payment" },
  { category: "withdrawals", title: "A withdrawal" },
  { category: "investments", title: "An investment" },
  { category: "identity", title: "My identity check" },
  { category: "account", title: "My account" },
  { category: "fees", title: "Fees or charges" },
  { category: "service", title: "How I was treated" },
  { category: "other", title: "Something else" },
];

/** Where a complaint stands, in the client's words. Staff's internal steps are not shown. */
export const complaintStatus: Record<ComplaintStatus, string> = {
  new: "Received",
  investigating: "Being looked into",
  waiting_for_client: "We need something from you",
  escalated: "With a senior member of the team",
  closed: "Closed",
};

export const complaintOutcome: Record<ComplaintOutcome, string> = {
  upheld: "We agreed with you",
  partly_upheld: "We agreed with part of it",
  not_upheld: "We did not agree",
};
