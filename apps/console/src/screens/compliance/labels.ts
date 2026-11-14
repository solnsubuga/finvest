import { formatExact } from "@/screens/products/format.ts";
import type { CaseOutcome, CaseSource, MatchCategory, RunReason, RunStatus, StandingOutcome } from "./api.ts";

export const SOURCE_LABELS: Record<CaseSource, string> = {
  screening: "Screening",
  monitoring: "Monitoring",
};

/** What raised a case: a screen's possible match, or a monitoring rule. */
export const CODE_LABELS: Record<string, { title: string; hint: string }> = {
  possible_match: {
    title: "Possible list match",
    hint: "A screen found a listed person or entity who may be this client.",
  },
  large_deposit: {
    title: "Large deposit",
    hint: "One deposit at or above UGX 20,000,000, the large cash transaction threshold.",
  },
  split_deposits: {
    title: "Split deposits",
    hint: "Deposits each under UGX 20,000,000 that together reached it within 7 days.",
  },
  many_deposits: {
    title: "Many deposits",
    hint: "5 deposits within 24 hours.",
  },
  unusual_deposit: {
    title: "Unusual deposit",
    hint: "A deposit at least 5 times the client's usual one over the last 180 days.",
  },
  quick_withdrawal: {
    title: "Quick withdrawal",
    hint: "A payout of at least half of what came in during the last 7 days.",
  },
};

export function codeTitle(code: string): string {
  return CODE_LABELS[code]?.title ?? code;
}

export const OUTCOME_LABELS: Record<CaseOutcome, { title: string; hint: string }> = {
  not_a_match: { title: "Not a match", hint: "The listed person is someone else. Say how you know, such as the date of birth or country." },
  pep_accepted: {
    title: "Accept as a politically exposed person",
    hint: "The client is a PEP, or family or a close associate, and stays a client. Write the enhanced due diligence done.",
  },
  sanctioned: {
    title: "Sanctioned",
    hint: "The client is on a sanctions list. Their payouts stay held for good and a report is owed to the FIA.",
  },
  no_concern: { title: "No concern", hint: "Looked into, and nothing to report. Say what explains it." },
  report_to_fia: {
    title: "Report to the FIA",
    hint: "A suspicious transaction report is owed. File it on the FIA's system and write its reference here.",
  },
};

export const STANDING_LABELS: Record<StandingOutcome, string> = {
  clear: "Clear",
  possible_match: "Possible match, case open",
  confirmed_match: "Confirmed sanctions match",
  unavailable: "Last screen failed",
  pending: "Being screened",
  not_run: "Not screened, no provider",
};

export const STANDING_TONES: Record<StandingOutcome, string> = {
  clear: "bg-success-surface text-success",
  possible_match: "bg-warning-surface text-warning-text",
  confirmed_match: "bg-danger-surface text-danger",
  unavailable: "bg-warning-surface text-warning-text",
  pending: "bg-surface-sunken text-ink",
  not_run: "bg-surface-sunken text-muted",
};

export const CATEGORY_LABELS: Record<MatchCategory, string> = {
  sanctions: "Sanctions",
  pep: "Politically exposed",
  adverse_media: "Adverse media",
  other: "Other list",
};

export const REASON_LABELS: Record<RunReason, string> = {
  onboarding: "At identity check hand-in",
  rescreen: "Monthly rescreen",
  payout: "Before a payout",
  staff: "Asked for by staff",
};

export const RUN_LABELS: Record<RunStatus, string> = {
  pending: "Waiting for the provider",
  clear: "Nothing found",
  possible_match: "Possible match",
  unavailable: "Provider did not answer",
  not_run: "Not run, no provider",
  no_subject: "No identity check to screen",
};

const UGX = (minor: unknown) => (typeof minor === "number" ? formatExact({ minor, currency: "UGX" }) : "Not given");

/** What a monitoring rule saw, as label and value rows for staff. */
export function detailRows(detail: Readonly<Record<string, unknown>>): { name: string; value: string }[] {
  const rows: { name: string; value: string }[] = [];
  const add = (key: string, name: string, format: (value: unknown) => string) => {
    if (key in detail) rows.push({ name, value: format(detail[key]) });
  };
  add("amount_minor", "Amount", UGX);
  add("total_minor", "Total in the window", UGX);
  add("deposits_minor", "Came in during the window", UGX);
  add("usual_minor", "Usual deposit (median)", UGX);
  add("threshold", "Threshold", UGX);
  add("multiple", "Times the usual", (value) => `${String(value)} times`);
  add("earlier_deposits", "Earlier deposits looked at", (value) => String(value));
  add("count", "Deposits", (value) => String(value));
  add("days", "Window", (value) => `${String(value)} days`);
  add("hours", "Window", (value) => `${String(value)} hours`);
  return rows;
}

/** The transaction IDs a rule saw. */
export function detailIds(detail: Readonly<Record<string, unknown>>): string[] {
  const ids: string[] = [];
  for (const key of ["deposit_id", "payout_id"]) {
    const value = detail[key];
    if (typeof value === "string") ids.push(value);
  }
  const many = detail.deposit_ids;
  if (Array.isArray(many)) ids.push(...many.filter((value): value is string => typeof value === "string"));
  return [...new Set(ids)];
}
