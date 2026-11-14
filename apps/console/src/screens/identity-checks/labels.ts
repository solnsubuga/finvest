import type { AutomaticCheckName, CheckStatus, DocumentType, PhotoKind, ScreeningStanding, Verdict } from "./api.ts";

export const DOCUMENT_LABELS: Record<DocumentType, string> = {
  national_id: "National ID",
  passport: "Passport",
  driving_permit: "Driving permit",
  refugee_id: "Refugee ID",
};

export const STATUS_LABELS: Record<CheckStatus, string> = {
  submitted: "Automatic checks running",
  needs_review: "Needs review",
  with_compliance: "With compliance",
  awaiting_client: "Waiting for client",
  approved: "Approved",
  rejected: "Rejected",
};

export const CHECK_LABELS: Record<AutomaticCheckName, string> = {
  document_genuine: "ID is genuine and not expired",
  selfie_match: "Selfie matches ID photo",
  screening: "Sanctions and political exposure",
  liveness: "Selfie taken live",
};

const VERDICT_LABELS: Record<AutomaticCheckName, Record<Verdict, string>> = {
  document_genuine: { pass: "Pass", fail: "Fail", unsure: "Unsure" },
  selfie_match: { pass: "Pass", fail: "No match", unsure: "Unsure" },
  screening: { pass: "Clear", fail: "Match found", unsure: "Possible match" },
  liveness: { pass: "Live", fail: "Live check failed", unsure: "Unsure" },
};

export function verdictLabel(check: AutomaticCheckName, verdict: Verdict, score: number | null): string {
  if (score !== null && verdict !== "pass") return `${score}%`;
  return VERDICT_LABELS[check][verdict];
}

const FLAG_LABELS: Record<string, string> = {
  no_automatic_checks: "No automatic checks",
  document_needs_person: "Document checked by a person",
  duplicate_document: "ID used on another account",
  document_genuine_fail: "ID did not pass",
  document_genuine_unsure: "ID could not be confirmed",
  document_genuine_not_run: "ID was not checked",
  selfie_match_fail: "Selfie did not match",
  selfie_match_unsure: "Selfie could not be confirmed",
  selfie_match_not_run: "Selfie was not checked",
  screening_fail: "Screening match",
  screening_unsure: "Possible screening match",
  screening_not_run: "Screening did not run",
  liveness_fail: "Live check failed, camera selfie sent",
  liveness_unsure: "Live check unclear",
  liveness_not_run: "Selfie not taken live",
  provider_result_unclear: "Provider result unclear",
  automatic_checks_passed: "Automatic checks passed",
  document_expired: "ID has expired",
  provider_refused: "Provider refused the check",
  provider_not_received: "Provider never received the check",
  provider_no_answer: "Provider did not answer",
};

export const SCREENING_LABELS: Record<ScreeningStanding | "none", string> = {
  clear: "Clear",
  possible_match: "Possible match",
  confirmed_match: "Match confirmed",
  unavailable: "Could not run",
  pending: "Running",
  not_run: "Not run",
  none: "Not screened",
};

/** Why a screening result other than clear needs a look before approving. Null when it is clear. */
export function screeningWarning(standing: ScreeningStanding | null): string | null {
  switch (standing) {
    case "clear":
      return null;
    case "possible_match":
      return "Screening found a possible match on a sanctions or political exposure list. Look at the case on the Compliance screen before approving.";
    case "confirmed_match":
      return "Compliance confirmed this client matches a sanctions or political exposure list. Do not approve without compliance.";
    case "unavailable":
      return "Screening could not finish, so this client has not been checked against the sanctions and political exposure lists. Do not approve until it has run: escalate to compliance to have the client screened again.";
    case "pending":
      return "Screening has not finished yet. Wait for its result before approving.";
    case "not_run":
      return "Screening did not run for this client, so they have not been checked against the sanctions and political exposure lists.";
    case null:
      return "This client has never been screened against the sanctions and political exposure lists.";
  }
}

/** The first reason a person is needed, in words. */
export function flagLabel(flags: readonly string[]): string | null {
  const first = flags[0];
  return first ? (FLAG_LABELS[first] ?? "Needs a person") : null;
}

export function photoLabel(documentType: DocumentType, kind: PhotoKind): string {
  if (kind === "selfie") return "Selfie";
  if (documentType === "passport") return "Passport, photo page";
  return `${DOCUMENT_LABELS[documentType]}, ${kind}`;
}

export const PHOTO_NAMES: Record<PhotoKind, string> = { front: "Front of the ID", back: "Back of the ID", selfie: "Selfie" };

const COUNTRIES: Record<string, string> = { UG: "Uganda", KE: "Kenya", TZ: "Tanzania", RW: "Rwanda" };

export function countryName(code: string): string {
  return COUNTRIES[code] ?? code;
}

/** "CM90123456784PL" becomes "CM90•••••••4PL": enough to compare at a glance. */
export function maskDocumentNumber(value: string): string {
  if (value.length <= 7) return "•".repeat(value.length);
  return value.slice(0, 4) + "•".repeat(value.length - 7) + value.slice(-3);
}

const DAY = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });

/** A calendar date such as "14 Mar 1990". */
export function formatDay(isoDate: string): string {
  return DAY.format(new Date(`${isoDate}T00:00:00Z`));
}

export function formatAccountNumber(value: string): string {
  return `${value.slice(0, 4)} ${value.slice(4, 7)} ${value.slice(7)}`;
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
