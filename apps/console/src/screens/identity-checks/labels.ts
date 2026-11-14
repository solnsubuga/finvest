import type { AutomaticCheckName, CheckStatus, DocumentType, PhoneName, PhotoKind, Verdict } from "./api.ts";

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
  superseded: "Replaced by a full check",
};

export const CHECK_LABELS: Record<AutomaticCheckName, string> = {
  document_genuine: "ID is genuine and not expired",
  selfie_match: "Selfie matches ID photo",
  screening: "Sanctions and political exposure",
  liveness: "Selfie taken live",
};

export const PHONE_NAME_LABEL = "Phone registered to this name";

const PHONE_NAME_VERDICTS: Record<PhoneName["status"], string> = {
  checking: "Checking",
  matches: "Matches",
  partly_matches: "Partly matches",
  different: "Different name",
  no_name: "No name on the number",
  not_answered: "Network did not answer",
};

/** The result, in words. The network's name is shown when it is not the same as the ID's. */
export function phoneNameVerdict(phone: PhoneName): string {
  const verdict = PHONE_NAME_VERDICTS[phone.status];
  return phone.registeredName && phone.status !== "matches" ? `${verdict}: ${phone.registeredName}` : verdict;
}

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
  phone_name_not_confirmed: "Phone name not confirmed",
};

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
