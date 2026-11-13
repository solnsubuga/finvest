/**
 * The console's view of announcements: words sent to every active client.
 *
 * The shapes follow the announcement routes in docs/notifications.md.
 * `http.ts` calls them; `announcementsStub.ts` is the sample for `pnpm dev`
 * and previews.
 */

import { outsideLinks } from "./rules.ts";

export type AnnouncementStatus = "proposed" | "approved" | "rejected" | "withdrawn";
export type AnnouncementDecision = "approved" | "rejected" | "withdrawn";

export interface Announcement {
  id: string;
  title: string;
  message: string;
  /** Why it is being sent. Staff only. */
  reason: string;
  status: AnnouncementStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  /** Why it was rejected. */
  decisionReason: string | null;
  /** Clients told so far. It grows while Finvest sends an approved one. */
  told: number;
  /** The signed-in member of staff proposed it, so they can withdraw it but not decide it. */
  yours: boolean;
}

export interface NewAnnouncement {
  title: string;
  message: string;
  reason: string;
}

export type AnnouncementField = "title" | "message" | "reason";
export type AnnouncementProblemCode =
  | "required"
  | "too_long"
  | "invalid_characters"
  | "stray_brace"
  | "outside_link"
  | "no_rates";

export interface AnnouncementProblem {
  field: AnnouncementField;
  code: AnnouncementProblemCode;
}

export interface AnnouncementsApi {
  /** The latest announcements, newest first. */
  list(): Promise<Announcement[]>;
  propose(announcement: NewAnnouncement): Promise<Announcement>;
  /** `approved`, which starts sending, or `rejected` with a reason, by someone else. `withdrawn` by the proposer. */
  decide(announcementId: string, decision: AnnouncementDecision, reason: string | null): Promise<Announcement>;
}

export type AnnouncementsErrorCode = "invalid" | "forbidden" | "not_found" | "not_connected" | "unavailable";

export class AnnouncementsError extends Error {
  constructor(
    readonly code: AnnouncementsErrorCode,
    /** For `invalid`: what the backend refused, field by field. */
    readonly problems: readonly AnnouncementProblem[] = [],
  ) {
    super(code);
    this.name = "AnnouncementsError";
  }
}

const MESSAGES: Record<AnnouncementsErrorCode, string> = {
  invalid: "Finvest refused these words. Fix what is marked and send them again.",
  forbidden: "An announcement needs a second person, or a role you do not have. Ask an admin.",
  not_found: "This announcement has already been decided. Here is the list as it now stands.",
  not_connected: "Announcements are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function announcementsErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof AnnouncementsError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new AnnouncementsError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedAnnouncementsApi: AnnouncementsApi = {
  list: notConnected,
  propose: notConnected,
  decide: notConnected,
};

/**
 * The same limits as `check_announcement` in the backend's `domain/wording.py`:
 * the message leaves room for "Finvest: " in one SMS.
 */
export const ANNOUNCEMENT_LIMITS: Record<AnnouncementField, number> = { title: 60, message: 291, reason: 500 };

function fieldProblems(field: AnnouncementField, value: string): AnnouncementProblemCode[] {
  if (value.trim().length === 0) return ["required"];
  const problems: AnnouncementProblemCode[] = [];
  if (value.length > ANNOUNCEMENT_LIMITS[field]) problems.push("too_long");
  if (field === "reason") return problems;
  // eslint-disable-next-line no-control-regex -- control characters are exactly what is refused
  if (value !== value.trim() || /[\u0000-\u001f]/.test(value)) problems.push("invalid_characters");
  if (/[{}]/.test(value)) problems.push("stray_brace");
  if (outsideLinks(value).length > 0) problems.push("outside_link");
  if (value.includes("%")) problems.push("no_rates");
  return problems;
}

/** What the backend would refuse, checked as the person types. The backend decides. */
export function checkAnnouncement(announcement: NewAnnouncement): AnnouncementProblem[] {
  const problems: AnnouncementProblem[] = (["title", "message", "reason"] as const).flatMap((field) =>
    fieldProblems(field, announcement[field]).map((code) => ({ field, code })),
  );
  // The message also goes by SMS, where anything outside the basic alphabet costs several messages.
  // eslint-disable-next-line no-control-regex -- the range is the basic alphabet
  if (!problems.some((problem) => problem.field === "message") && !/^[\u0000-\u007f]*$/.test(announcement.message)) {
    problems.push({ field: "message", code: "invalid_characters" });
  }
  return problems;
}

export function announcementProblemText(code: AnnouncementProblemCode, field: AnnouncementField): string {
  switch (code) {
    case "required":
      return "Write something here.";
    case "too_long":
      return `Keep it to ${ANNOUNCEMENT_LIMITS[field]} characters.`;
    case "invalid_characters":
      return field === "message"
        ? "Use plain letters, digits and punctuation, with no line breaks, so it goes as one text."
        : "Remove the line breaks and the spaces at the start or end.";
    case "stray_brace":
      return "An announcement has no values to fill in. Remove the braces.";
    case "outside_link":
      return "Link only to finvestug.com.";
    case "no_rates":
      return "Remove the percent sign. Clients see net amounts, never a rate.";
  }
}
