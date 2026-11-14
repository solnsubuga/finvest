import { DocumentKind, OrganisationKind, Problem, MIN_APPROVALS, MIN_SIGNATORIES } from "./types";

/**
 * The backend's rules, for the demo stand-in and for checking before sending.
 * The backend checks again: see backend/app/modules/organisations/domain/applications.py.
 */

export const MAX_NAME = 120;
export const MAX_ROLE = 60;

export const DOCUMENTS: Record<OrganisationKind, DocumentKind[]> = {
  company: ["certificate_of_incorporation", "board_resolution"],
  group: ["constitution", "group_resolution"],
};

const NAME = /^[\p{L}\p{N}][\p{L}\p{N}\p{M}_ &'.,()-]*$/u;
// A dot straight before a letter, as in "x.com", or seven digits in a row: an address or a number to call.
const ADDRESS_LIKE = /\.\p{L}|(?:\d[ -]?){6}\d/u;
const REGISTRATION = /^[A-Z0-9][A-Z0-9/ -]{2,38}[A-Z0-9]$/;

export type NameCheck = { name: string } | { problem: "required" | "not_a_name" };

export function checkName(value: string): NameCheck {
  const cleaned = value.replace(/’/g, "'").split(/\s+/).filter(Boolean).join(" ");
  if (!cleaned || cleaned.length > MAX_NAME) return { problem: "required" };
  if (!NAME.test(cleaned) || ADDRESS_LIKE.test(cleaned)) return { problem: "not_a_name" };
  return { name: cleaned };
}

/** The registration number in capitals, null when none was given, or "invalid". */
export function checkRegistration(value: string): string | null | "invalid" {
  const cleaned = value.toUpperCase().split(/\s+/).filter(Boolean).join(" ");
  if (!cleaned) return null;
  return REGISTRATION.test(cleaned) ? cleaned : "invalid";
}

export function problemsOf(found: {
  kind: OrganisationKind;
  registrationNumber: string | null;
  approvalsNeeded: number;
  joined: number;
  invited: number;
  documents: DocumentKind[];
}): Problem[] {
  const out: Problem[] = [];
  if (found.invited) out.push("signatories_waiting");
  if (found.joined < MIN_SIGNATORIES[found.kind]) out.push("too_few_signatories");
  if (found.approvalsNeeded > found.joined) out.push("approvals_more_than_signatories");
  if (found.approvalsNeeded < MIN_APPROVALS[found.kind]) out.push("approvals_too_few");
  if (!DOCUMENTS[found.kind].every((kind) => found.documents.includes(kind))) out.push("documents_missing");
  if (found.kind === "company" && !found.registrationNumber) out.push("registration_number_missing");
  return out;
}
