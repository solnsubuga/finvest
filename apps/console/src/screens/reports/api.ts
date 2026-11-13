/**
 * The audit trail as the Reports and audit screen sees it.
 *
 * The trail is redacted when it is written, so an event carries identifiers, a reason and
 * details, never personal data. Reading and exporting need `audit.view`, and the API checks
 * it again. Every export is itself recorded on the trail.
 */

import { ApiError } from "@/api/http.ts";

export const CATEGORIES = ["change", "access", "approval", "operator_control", "security"] as const;
export const OUTCOMES = ["succeeded", "denied", "failed"] as const;
export const ACTOR_KINDS = ["client", "staff", "system", "provider", "anonymous"] as const;

export type Category = (typeof CATEGORIES)[number];
export type Outcome = (typeof OUTCOMES)[number];
export type ActorKind = (typeof ACTOR_KINDS)[number];

/** What the reader narrowed the trail to. Dates are Kampala days, `YYYY-MM-DD`, both inclusive. */
export interface AuditFilters {
  category?: Category;
  action?: string;
  outcome?: Outcome;
  actorKind?: ActorKind;
  actorId?: string;
  resourceType?: string;
  resourceId?: string;
  correlationId?: string;
  from?: string;
  to?: string;
}

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export interface AuditEvent {
  id: string;
  occurredAt: string;
  category: Category;
  action: string;
  outcome: Outcome;
  actorKind: ActorKind;
  actorId: string | null;
  /** The member of staff's name. Clients are shown by ID only. */
  actorName: string | null;
  resourceType: string;
  resourceId: string | null;
  reason: string | null;
  correlationId: string | null;
  details: Record<string, JsonValue>;
}

export interface AuditPage {
  events: AuditEvent[];
  /** Pass back for the next, older page. Null on the last page. */
  nextCursor: string | null;
}

export interface Facets {
  actions: string[];
  resourceTypes: string[];
}

export interface AuditExport {
  file: Blob;
  filename: string;
}

export interface AuditApi {
  events(filters: AuditFilters, cursor?: string): Promise<AuditPage>;
  facets(): Promise<Facets>;
  exportCsv(filters: AuditFilters): Promise<AuditExport>;
}

export type AuditErrorCode = "invalid" | "too_many" | "forbidden" | "not_connected" | "unavailable";

export class AuditError extends Error {
  constructor(
    readonly code: AuditErrorCode,
    /** For `invalid`: the filter the API refused, such as `actor_id`. */
    readonly field: string | null = null,
  ) {
    super(code);
    this.name = "AuditError";
  }
}

const FIELD_LABELS: Record<string, string> = {
  action: "action",
  actor_id: "person or client ID",
  resource_type: "record type",
  resource_id: "record ID",
  correlation_id: "request ID",
  since: "from date",
  until: "to date",
  cursor: "page",
};

export function auditErrorMessage(error: unknown): string {
  if (error instanceof AuditError) {
    switch (error.code) {
      case "invalid":
        return error.field === "until"
          ? "The to date must be on or after the from date."
          : `That ${FIELD_LABELS[error.field ?? ""] ?? "filter"} is not in the right form. Check it and try again.`;
      case "too_many":
        return "More than 10,000 events match, which is too many for one file. Narrow the dates or the filters and export again.";
      case "forbidden":
        return "Your role cannot read the audit trail.";
      case "not_connected":
        return "This console is not connected to Finvest yet.";
      case "unavailable":
        return "Finvest could not be reached. Check your connection and try again.";
    }
  }
  return "Something went wrong. Try again.";
}

/** Turns what the API answered into the screen's own error. */
export function toAuditError(error: unknown): AuditError {
  if (error instanceof AuditError) return error;
  if (error instanceof ApiError) {
    if (error.code === "forbidden" || error.status === 403) return new AuditError("forbidden");
    if (error.code === "validation_failed") {
      if (error.fieldCode === "too_many_events") return new AuditError("too_many");
      const path = error.fields[0]?.field_path ?? [];
      const field = path.length > 1 ? String(path[path.length - 1]) : null;
      return new AuditError("invalid", field);
    }
  }
  return new AuditError("unavailable");
}

const refuse = () => Promise.reject(new AuditError("not_connected"));

export const notConnectedAuditApi: AuditApi = {
  events: refuse,
  facets: refuse,
  exportCsv: refuse,
};

const PAD = (value: number) => String(value).padStart(2, "0");

/** A Kampala day's start, as the API wants it. Kampala is UTC+3 all year. */
export function kampalaStart(day: string): string {
  return `${day}T00:00:00+03:00`;
}

/** The start of the Kampala day after `day`, so a `to` date takes in the whole day. */
export function kampalaDayAfter(day: string): string {
  const [year, month, date] = day.split("-").map(Number) as [number, number, number];
  const next = new Date(Date.UTC(year, month - 1, date + 1));
  return kampalaStart(`${next.getUTCFullYear()}-${PAD(next.getUTCMonth() + 1)}-${PAD(next.getUTCDate())}`);
}

/** The export's file name, stamped with the Kampala time it was made. */
export function exportFilename(now: Date): string {
  const kampala = new Date(now.getTime() + 3 * 60 * 60 * 1000);
  const stamp =
    `${kampala.getUTCFullYear()}${PAD(kampala.getUTCMonth() + 1)}${PAD(kampala.getUTCDate())}` +
    `-${PAD(kampala.getUTCHours())}${PAD(kampala.getUTCMinutes())}${PAD(kampala.getUTCSeconds())}`;
  return `finvest-audit-${stamp}.csv`;
}
