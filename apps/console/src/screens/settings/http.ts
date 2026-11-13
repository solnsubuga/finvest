/**
 * Wordings and announcements over the real API (docs/notifications.md).
 *
 * The wording routes answer a proposal or a decision with the proposal alone. The screen
 * wants the message as it now stands, so each write is followed by a fresh list.
 */

import { ApiError, type Http } from "@/api/http.ts";
import {
  AnnouncementsError,
  type Announcement,
  type AnnouncementField,
  type AnnouncementProblem,
  type AnnouncementProblemCode,
  type AnnouncementsApi,
} from "./announcements.ts";
import { WordingsError, type Category, type Field, type Problem, type ProblemCode, type Wording, type WordingsApi, type WordingText } from "./api.ts";

interface ProposalJson {
  id: string;
  wording: WordingText;
  preview: WordingText;
  reason: string;
  proposed_by: string;
  proposed_at: string;
  yours: boolean;
}

interface WordingJson {
  event: string;
  category: Category;
  variables: string[];
  wording: WordingText;
  preview: WordingText;
  edited: boolean;
  changed_at: string | null;
  proposal: ProposalJson | null;
}

interface AnnouncementJson {
  id: string;
  title: string;
  message: string;
  reason: string;
  status: Announcement["status"];
  proposed_by: string;
  proposed_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  told: number;
  yours: boolean;
}

const text = (json: WordingText): WordingText => ({ title: json.title, body: json.body, sms: json.sms });

export function toWording(json: WordingJson): Wording {
  return {
    event: json.event,
    category: json.category,
    variables: json.variables,
    wording: text(json.wording),
    preview: text(json.preview),
    edited: json.edited,
    changedAt: json.changed_at,
    proposal: json.proposal
      ? {
          id: json.proposal.id,
          wording: text(json.proposal.wording),
          preview: text(json.proposal.preview),
          reason: json.proposal.reason,
          proposedBy: json.proposal.proposed_by,
          proposedAt: json.proposal.proposed_at,
          yours: json.proposal.yours,
        }
      : null,
  };
}

export function toAnnouncement(json: AnnouncementJson): Announcement {
  return {
    id: json.id,
    title: json.title,
    message: json.message,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionReason: json.decision_reason,
    told: json.told,
    yours: json.yours,
  };
}

const WORDING_FIELDS: readonly string[] = ["title", "body", "sms"] satisfies Field[];
const WORDING_CODES: readonly string[] = [
  "required",
  "too_long",
  "invalid_characters",
  "unknown_placeholder",
  "repeated_placeholder",
  "stray_brace",
  "outside_link",
  "no_rates",
] satisfies ProblemCode[];
const ANNOUNCEMENT_FIELDS: readonly string[] = ["title", "message", "reason"] satisfies AnnouncementField[];
const ANNOUNCEMENT_CODES: readonly string[] = [
  "required",
  "too_long",
  "invalid_characters",
  "stray_brace",
  "outside_link",
  "no_rates",
] satisfies AnnouncementProblemCode[];

/** The field problems the screen can mark, by the last part of each path. */
function problems<T>(error: ApiError, fields: readonly string[], codes: readonly string[]): T[] {
  return error.fields.flatMap((problem) => {
    const field = problem.field_path[problem.field_path.length - 1];
    // Pydantic's own codes: `string_too_long`, `missing` and `string_too_short` are known; anything else
    // is shown as not accepted rather than guessed.
    const code = codes.includes(problem.code)
      ? problem.code
      : problem.code === "string_too_long"
        ? "too_long"
        : problem.code === "missing" || problem.code === "string_too_short"
          ? "required"
          : "not_accepted";
    return typeof field === "string" && fields.includes(field) ? [{ field, code } as T] : [];
  });
}

/** `deciding` is true for a decision, where a bare refusal means the proposer tried to decide. */
export function toWordingsError(error: unknown, deciding = false): WordingsError {
  if (error instanceof WordingsError) return error;
  if (!(error instanceof ApiError)) return new WordingsError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new WordingsError("invalid", problems<Problem>(error, WORDING_FIELDS, WORDING_CODES));
    case "conflict":
      return new WordingsError("conflict");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case "confirm_it_is_you":
      return new WordingsError("not_confirmed");
    case "forbidden":
      return new WordingsError(deciding ? "same_person" : "forbidden");
    case "not_found":
      return new WordingsError("not_found");
    default:
      return new WordingsError("unavailable");
  }
}

export function toAnnouncementsError(error: unknown): AnnouncementsError {
  if (error instanceof AnnouncementsError) return error;
  if (!(error instanceof ApiError)) return new AnnouncementsError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new AnnouncementsError("invalid", problems<AnnouncementProblem>(error, ANNOUNCEMENT_FIELDS, ANNOUNCEMENT_CODES));
    case "confirm_it_is_you":
      return new AnnouncementsError("not_confirmed");
    case "forbidden":
      return new AnnouncementsError("forbidden");
    case "not_found":
      return new AnnouncementsError("not_found");
    default:
      return new AnnouncementsError("unavailable");
  }
}

export function createHttpWordingsApi(http: Http): WordingsApi {
  // Which message each open proposal changes, so a decision can show that message afterwards.
  const proposalEvents = new Map<string, string>();

  async function list(): Promise<Wording[]> {
    const json = await http.request<{ wordings: WordingJson[] }>("GET", "/staff/notification-wordings");
    const wordings = json.wordings.map(toWording);
    for (const wording of wordings) if (wording.proposal) proposalEvents.set(wording.proposal.id, wording.event);
    return wordings;
  }

  async function current(event: string): Promise<Wording> {
    const found = (await list()).find((wording) => wording.event === event);
    if (!found) throw new WordingsError("not_found");
    return found;
  }

  /**
   * The message after a change went through. If it cannot be read, say `stale`, so the screen
   * reloads its list and shows what Finvest now holds, rather than "could not reach Finvest"
   * followed by a refusal when the person tries again.
   */
  async function afterWrite(event: string): Promise<Wording> {
    try {
      return await current(event);
    } catch {
      throw new WordingsError("stale");
    }
  }

  return {
    async list() {
      try {
        return await list();
      } catch (error) {
        throw toWordingsError(error);
      }
    },
    async propose(event, wording, reason) {
      try {
        await http.request("POST", `/staff/notification-wordings/${encodeURIComponent(event)}/proposals`, { wording, reason });
      } catch (error) {
        throw toWordingsError(error);
      }
      return afterWrite(event);
    },
    async decide(proposalId, decision, reason) {
      const event = proposalEvents.get(proposalId);
      try {
        await http.request("POST", `/staff/notification-wordings/proposals/${encodeURIComponent(proposalId)}/decision`, { decision, reason });
      } catch (error) {
        throw toWordingsError(error, decision !== "withdrawn");
      }
      proposalEvents.delete(proposalId);
      if (!event) throw new WordingsError("not_found");
      return afterWrite(event);
    },
  };
}

export function createHttpAnnouncementsApi(http: Http): AnnouncementsApi {
  return {
    async list() {
      try {
        const json = await http.request<{ announcements: AnnouncementJson[] }>("GET", "/staff/announcements");
        return json.announcements.map(toAnnouncement);
      } catch (error) {
        throw toAnnouncementsError(error);
      }
    },
    async propose(announcement) {
      try {
        return toAnnouncement(await http.request<AnnouncementJson>("POST", "/staff/announcements", announcement));
      } catch (error) {
        throw toAnnouncementsError(error);
      }
    },
    async decide(announcementId, decision, reason) {
      try {
        return toAnnouncement(
          await http.request<AnnouncementJson>("POST", `/staff/announcements/${encodeURIComponent(announcementId)}/decision`, {
            decision,
            reason,
          }),
        );
      } catch (error) {
        throw toAnnouncementsError(error);
      }
    },
  };
}
