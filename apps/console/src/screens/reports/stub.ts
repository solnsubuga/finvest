/**
 * A sample audit trail for `pnpm dev` and preview builds, filtered and paged the way the API does.
 * Nothing here is real: the names are the stub team's and the IDs are made up.
 */

import {
  AuditError,
  exportFilename,
  kampalaDayAfter,
  kampalaStart,
  type AuditApi,
  type AuditEvent,
  type AuditFilters,
  type JsonValue,
} from "./api.ts";

const PAGE_SIZE = 50;
const EXPORT_LIMIT = 10_000;

interface Me {
  id: string;
  name: string;
}

type Template = Omit<AuditEvent, "id" | "occurredAt" | "correlationId">;

function templates(me: Me): Template[] {
  const grace = { actorKind: "staff" as const, actorId: "-NstubGraceAuma00000", actorName: "Grace Auma" };
  const daniel = { actorKind: "staff" as const, actorId: "-NstubDanielKato0000", actorName: "Daniel Kato" };
  const ruth = { actorKind: "staff" as const, actorId: "-NstubRuthNamata0000", actorName: "Ruth Namata" };
  const self = { actorKind: "staff" as const, actorId: me.id, actorName: me.name };
  const client = { actorKind: "client" as const, actorId: "-NstubClient00000042", actorName: null };
  const system = { actorKind: "system" as const, actorId: null, actorName: null };
  const provider = { actorKind: "provider" as const, actorId: null, actorName: null };
  const none: Record<string, JsonValue> = {};
  return [
    { ...grace, category: "change", action: "identity.team.change_proposed", outcome: "succeeded", resourceType: "staff_change", resourceId: "-NstubChange00000001", reason: "Moving to the operations desk", details: { kind: "change_role", role: "operations" } },
    { ...self, category: "approval", action: "identity.team.change_approved", outcome: "succeeded", resourceType: "staff_change", resourceId: "-NstubChange00000000", reason: "Agreed at the Monday meeting", details: { kind: "invite" } },
    { ...client, category: "security", action: "identity.client.signed_in", outcome: "succeeded", resourceType: "client", resourceId: "-NstubClient00000042", reason: null, details: { method: "passkey" } },
    { ...daniel, category: "access", action: "identity.staff.denied", outcome: "denied", resourceType: "route", resourceId: "/staff/audit/events", reason: null, details: { permission: "audit.view" } },
    { ...ruth, category: "change", action: "notifications.suppression.lift_proposed", outcome: "succeeded", resourceType: "email_suppression", resourceId: "8f3a1c0d", reason: "Client fixed their inbox", details: none },
    { ...grace, category: "approval", action: "notifications.suppression.lift_approved", outcome: "succeeded", resourceType: "email_suppression", resourceId: "8f3a1c0d", reason: "Confirmed on a call", details: none },
    { ...system, category: "operator_control", action: "reconciliation.day.closed", outcome: "succeeded", resourceType: "reconciliation_day", resourceId: "2026-11-12", reason: null, details: { lines: 184, unmatched: 0 } },
    { ...provider, category: "change", action: "payments.collection.confirmed", outcome: "succeeded", resourceType: "collection", resourceId: "-NstubCollection0007", reason: null, details: { provider: "iotec" } },
    { ...self, category: "change", action: "identity.client.lock_proposed", outcome: "succeeded", resourceType: "client", resourceId: "-NstubClient00000017", reason: "Client reported a lost phone", details: { lock: true } },
    { ...daniel, category: "security", action: "identity.staff.sign_in_failed", outcome: "failed", resourceType: "staff", resourceId: "-NstubDanielKato0000", reason: null, details: { step: "authenticator" } },
    { ...ruth, category: "access", action: "audit.trail_exported", outcome: "succeeded", resourceType: "audit_trail", resourceId: null, reason: null, details: { events: 212, filters: { category: "approval" } } },
    { ...grace, category: "change", action: "investing.rates.change_proposed", outcome: "succeeded", resourceType: "rate_change", resourceId: "-NstubRate0000000003", reason: "Treasury auction results", details: { product: "tbill_364" } },
  ];
}

/** Sixty events, newest first, every 40 minutes back from `now`. */
function sample(me: Me, now: number): AuditEvent[] {
  const all = templates(me);
  return Array.from({ length: 60 }, (_, index) => {
    const template = all[index % all.length] as Template;
    const number = String(60 - index).padStart(4, "0");
    return {
      ...template,
      id: `-NstubAuditEvent${number}`,
      occurredAt: new Date(now - (index * 40 + 7) * 60 * 1000).toISOString(),
      correlationId: `req_stub${number}`,
    };
  });
}

function matches(event: AuditEvent, filters: AuditFilters): boolean {
  const action = filters.action?.trim();
  if (filters.category && event.category !== filters.category) return false;
  if (action && !(action.endsWith(".") ? event.action.startsWith(action) : event.action === action)) return false;
  if (filters.outcome && event.outcome !== filters.outcome) return false;
  if (filters.actorKind && event.actorKind !== filters.actorKind) return false;
  if (filters.actorId?.trim() && event.actorId !== filters.actorId.trim()) return false;
  if (filters.resourceType?.trim() && event.resourceType !== filters.resourceType.trim()) return false;
  if (filters.resourceId?.trim() && event.resourceId !== filters.resourceId.trim()) return false;
  if (filters.correlationId?.trim() && event.correlationId !== filters.correlationId.trim()) return false;
  const at = Date.parse(event.occurredAt);
  if (filters.from && at < Date.parse(kampalaStart(filters.from))) return false;
  if (filters.to && at >= Date.parse(kampalaDayAfter(filters.to))) return false;
  return true;
}

function check(filters: AuditFilters) {
  if (filters.actorId?.trim() && !/^[-_0-9A-Za-z]{20}$/.test(filters.actorId.trim())) throw new AuditError("invalid", "actor_id");
  if (filters.from && filters.to && filters.to < filters.from) throw new AuditError("invalid", "until");
}

const CSV_COLUMNS = ["occurred_at", "category", "action", "outcome", "actor_kind", "actor_id", "actor_name", "resource_type", "resource_id", "reason", "correlation_id", "details"];

function cell(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
}

export function createStubAuditApi(me: Me, now: () => number = Date.now, options: { exportLimit?: number; empty?: boolean } = {}): AuditApi {
  const events = options.empty ? [] : sample(me, now());
  const exportLimit = options.exportLimit ?? EXPORT_LIMIT;
  const wait = () => new Promise((resolve) => setTimeout(resolve, 150));

  return {
    async events(filters, cursor) {
      await wait();
      check(filters);
      const found = events.filter((event) => matches(event, filters));
      const start = cursor ? found.findIndex((event) => event.id === cursor) + 1 : 0;
      const page = found.slice(start, start + PAGE_SIZE);
      const last = page[page.length - 1];
      return { events: page, nextCursor: start + PAGE_SIZE < found.length && last ? last.id : null };
    },
    async facets() {
      await wait();
      return {
        actions: [...new Set(events.map((event) => event.action))].sort(),
        resourceTypes: [...new Set(events.map((event) => event.resourceType))].sort(),
      };
    },
    async exportCsv(filters) {
      await wait();
      check(filters);
      const found = events.filter((event) => matches(event, filters));
      if (found.length > exportLimit) throw new AuditError("too_many");
      const rows = found.map((event) =>
        [
          event.occurredAt,
          event.category,
          event.action,
          event.outcome,
          event.actorKind,
          event.actorId ?? "",
          event.actorName ?? "",
          event.resourceType,
          event.resourceId ?? "",
          event.reason ?? "",
          event.correlationId ?? "",
          JSON.stringify(event.details),
        ]
          .map(cell)
          .join(","),
      );
      const file = new Blob([[CSV_COLUMNS.join(","), ...rows].join("\r\n") + "\r\n"], { type: "text/csv" });
      return { file, filename: exportFilename(new Date(now())) };
    },
  };
}
