import type { Http } from "@/api/http.ts";
import {
  exportFilename,
  kampalaDayAfter,
  kampalaStart,
  toAuditError,
  type AuditApi,
  type AuditEvent,
  type AuditFilters,
  type JsonValue,
} from "./api.ts";

interface EventJson {
  id: string;
  occurred_at: string;
  category: AuditEvent["category"];
  action: string;
  outcome: AuditEvent["outcome"];
  actor_kind: AuditEvent["actorKind"];
  actor_id: string | null;
  actor_name: string | null;
  resource_type: string;
  resource_id: string | null;
  reason: string | null;
  correlation_id: string | null;
  details: Record<string, JsonValue>;
}

function toEvent(json: EventJson): AuditEvent {
  return {
    id: json.id,
    occurredAt: json.occurred_at,
    category: json.category,
    action: json.action,
    outcome: json.outcome,
    actorKind: json.actor_kind,
    actorId: json.actor_id,
    actorName: json.actor_name,
    resourceType: json.resource_type,
    resourceId: json.resource_id,
    reason: json.reason,
    correlationId: json.correlation_id,
    details: json.details,
  };
}

/** The filters as the API's query string, leaving out what is not set. */
export function toQuery(filters: AuditFilters, cursor?: string): string {
  const query = new URLSearchParams();
  const put = (name: string, value: string | undefined) => {
    const trimmed = value?.trim();
    if (trimmed) query.set(name, trimmed);
  };
  put("category", filters.category);
  put("action", filters.action);
  put("outcome", filters.outcome);
  put("actor_kind", filters.actorKind);
  put("actor_id", filters.actorId);
  put("resource_type", filters.resourceType);
  put("resource_id", filters.resourceId);
  put("correlation_id", filters.correlationId);
  if (filters.from) query.set("since", kampalaStart(filters.from));
  if (filters.to) query.set("until", kampalaDayAfter(filters.to));
  if (cursor) query.set("cursor", cursor);
  const text = query.toString();
  return text ? `?${text}` : "";
}

export function createHttpAuditApi(http: Http, now: () => Date = () => new Date()): AuditApi {
  return {
    async events(filters, cursor) {
      try {
        const page = await http.request<{ events: EventJson[]; next_cursor: string | null }>(
          "GET",
          `/staff/audit/events${toQuery(filters, cursor)}`,
        );
        return { events: page.events.map(toEvent), nextCursor: page.next_cursor };
      } catch (error) {
        throw toAuditError(error);
      }
    },
    async facets() {
      try {
        const facets = await http.request<{ actions: string[]; resource_types: string[] }>("GET", "/staff/audit/facets");
        return { actions: facets.actions, resourceTypes: facets.resource_types };
      } catch (error) {
        throw toAuditError(error);
      }
    },
    async exportCsv(filters) {
      try {
        const file = await http.download(`/staff/audit/events.csv${toQuery(filters)}`);
        return { file, filename: exportFilename(now()) };
      } catch (error) {
        throw toAuditError(error);
      }
    },
  };
}
