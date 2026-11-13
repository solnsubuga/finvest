import type { ActorKind, AuditEvent, Category, JsonValue, Outcome } from "./api.ts";

export const CATEGORY_LABELS: Record<Category, string> = {
  change: "Change",
  access: "Access",
  approval: "Approval",
  operator_control: "Operator control",
  security: "Security",
};

export const OUTCOME_LABELS: Record<Outcome, string> = {
  succeeded: "Succeeded",
  denied: "Denied",
  failed: "Failed",
};

export const ACTOR_KIND_LABELS: Record<ActorKind, string> = {
  client: "Client",
  staff: "Staff",
  system: "Finvest itself",
  provider: "A provider",
  anonymous: "Not signed in",
};

const TIME = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZone: "Africa/Kampala",
});

export function formatTime(iso: string): string {
  return TIME.format(new Date(iso));
}

/** Who did it, in words: a name for staff, an ID for a client. */
export function describeActor(event: AuditEvent): string {
  if (event.actorKind === "staff") return event.actorName ?? event.actorId ?? "A member of staff";
  if (event.actorKind === "client") return event.actorId ? `Client ${event.actorId}` : "A client";
  return ACTOR_KIND_LABELS[event.actorKind];
}

/** The label for an event's actor ID. Only clients and staff carry one today; the rest are named for safety. */
export function actorIdLabel(kind: ActorKind): string {
  if (kind === "client") return "Client ID";
  if (kind === "staff") return "Staff ID";
  return "Actor ID";
}

/** Who "Everything by …" narrows to. */
export function narrowByActorLabel(event: AuditEvent): string {
  if (event.actorKind === "staff") return describeActor(event);
  if (event.actorKind === "client") return "this client";
  return "this actor";
}

/** A detail's value on one line. */
export function formatDetail(value: JsonValue): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

