import { describe, expect, it } from "vitest";
import { ACTOR_KINDS, type ActorKind, type AuditEvent } from "./api.ts";
import { actorIdLabel, narrowByActorLabel } from "./labels.ts";

function event(actorKind: ActorKind, actorName: string | null = null): AuditEvent {
  return {
    id: "evt",
    occurredAt: "2026-11-13T09:00:00Z",
    category: "change",
    action: "test.action",
    outcome: "succeeded",
    actorKind,
    actorId: "-NxActor000000000001",
    actorName,
    resourceType: "thing",
    resourceId: null,
    correlationId: null,
    reason: null,
    details: {},
  };
}

describe("actor labels", () => {
  it("names the ID by who acted", () => {
    expect(actorIdLabel("client")).toBe("Client ID");
    expect(actorIdLabel("staff")).toBe("Staff ID");
    for (const kind of ["system", "provider", "anonymous"] as const) expect(actorIdLabel(kind)).toBe("Actor ID");
  });

  it("never calls a system or provider event a client's or staff member's", () => {
    expect(narrowByActorLabel(event("staff", "Grace Namata"))).toBe("Grace Namata");
    expect(narrowByActorLabel(event("client"))).toBe("this client");
    for (const kind of ACTOR_KINDS.filter((k) => k !== "client" && k !== "staff")) {
      expect(narrowByActorLabel(event(kind))).toBe("this actor");
      expect(actorIdLabel(kind)).not.toMatch(/Client|Staff/);
    }
  });
});
