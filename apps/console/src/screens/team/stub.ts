import { STUB_ROLE_PERMISSIONS } from "@/auth/stub.ts";
import { ROLES, type Role } from "@/auth/types.ts";
import { TeamError, type AccessSync, type Change, type Decision, type Member, type NewChange, type Team, type TeamApi } from "./api.ts";

/**
 * A sample team for `pnpm dev` and tests. It keeps the rules the backend
 * enforces, so the screen's refusals can be seen: two different admins, no
 * change to your own account, at least one active admin, one waiting change
 * per person. Names are the sample people drawn on the canvas.
 */

interface Me {
  id: string;
  name: string;
}

const OTHER_ADMIN = { id: "sample-admin-2", name: "Grace Auma" };

function sampleMembers(me: Me): Member[] {
  return [
    member(me.id, me.name, "solomon@finvestug.com", "admin", "2026-09-01T08:00:00Z", "2026-11-13T07:55:00Z"),
    member(OTHER_ADMIN.id, OTHER_ADMIN.name, "grace@finvestug.com", "admin", "2026-09-01T08:05:00Z", "2026-11-12T16:20:00Z"),
    member("sample-ops", "Daniel Kato", "daniel@finvestug.com", "operations", "2026-09-14T09:00:00Z", "2026-11-13T08:10:00Z"),
    member("sample-finance", "Ruth Namata", "ruth@finvestug.com", "finance", "2026-10-02T09:00:00Z", "2026-11-12T11:45:00Z", { passkey: false }),
    member("sample-compliance", "Peter Okello", "peter@finvestug.com", "compliance", "2026-10-05T09:00:00Z", "2026-11-10T14:00:00Z", { authenticator: false }),
    member("sample-support", "Amina Kintu", "amina@finvestug.com", "support", "2026-10-20T09:00:00Z", "2026-11-13T08:30:00Z"),
    member("sample-left", "Joseph Mukasa", "joseph@finvestug.com", "support", "2026-09-20T09:00:00Z", "2026-10-30T17:00:00Z", { status: "disabled" }),
  ];
}

function member(
  id: string,
  name: string,
  email: string,
  role: Role,
  createdAt: string,
  lastSignedInAt: string | null,
  { passkey = true, authenticator = true, status = "active" }: { passkey?: boolean; authenticator?: boolean; status?: Member["status"] } = {},
): Member {
  const access: AccessSync = { state: status === "disabled" ? "removed" : "in_access", errorCode: null, requestedAt: createdAt, syncedAt: createdAt };
  return { id, name, email, role, status, hasPasskey: passkey, hasAuthenticator: authenticator, createdAt, lastSignedInAt, access };
}

/** `access` shows each way Cloudflare Access can stand: failed, trying again, off, never checked. */
export type TeamSample = "sample" | "quiet" | "access";

function withAccessProblems(members: Member[]): void {
  const set = (id: string, access: AccessSync | null) => {
    const found = members.find((m) => m.id === id);
    if (found) found.access = access;
  };
  set("sample-finance", { state: "failed", errorCode: "cloudflare_token_refused", requestedAt: "2026-11-13T08:00:00Z", syncedAt: null });
  set("sample-compliance", { state: "waiting", errorCode: "cloudflare_unavailable", requestedAt: "2026-11-13T08:05:00Z", syncedAt: null });
  set("sample-support", { state: "not_set_up", errorCode: null, requestedAt: "2026-11-13T08:10:00Z", syncedAt: null });
  set("sample-left", { state: "waiting", errorCode: null, requestedAt: "2026-11-13T08:12:00Z", syncedAt: "2026-10-30T17:00:00Z" });
  set("sample-ops", null);
}

export function createStubTeamApi(me: Me, now: () => number = Date.now, sample: TeamSample = "sample"): TeamApi {
  const members = sampleMembers(me);
  if (sample === "access") withAccessProblems(members);
  const changes: Change[] = [];
  let count = 0;
  const nextId = () => `sample-change-${++count}`;
  const stamp = () => new Date(now()).toISOString();

  if (sample === "sample") {
    changes.push({
      id: nextId(),
      kind: "change_role",
      staffId: "sample-support",
      staffName: "Amina Kintu",
      email: null,
      role: "operations",
      currentRole: "support",
      reason: "Moving to the payments desk from Monday.",
      proposedBy: OTHER_ADMIN.id,
      proposedByName: OTHER_ADMIN.name,
      proposedAt: "2026-11-13T07:40:00Z",
      status: "proposed",
      yours: false,
      aboutYou: false,
    });
    changes.push({
      id: nextId(),
      kind: "invite",
      staffId: "sample-invited",
      staffName: "Brenda Achieng",
      email: "brenda@finvestug.com",
      role: "compliance",
      currentRole: null,
      reason: "New compliance officer, starts 17 November.",
      proposedBy: me.id,
      proposedByName: me.name,
      proposedAt: "2026-11-13T08:15:00Z",
      status: "proposed",
      yours: true,
      aboutYou: false,
    });
  }

  const activeAdmins = () => members.filter((m) => m.role === "admin" && m.status === "active").length;

  function check(change: Pick<Change, "kind" | "staffId" | "role">, acting: string): void {
    if (change.kind === "invite") return;
    const target = members.find((m) => m.id === change.staffId);
    if (!target) throw new TeamError("not_found");
    if (target.id === acting) throw new TeamError("own_account");
    const losesAdmin =
      target.role === "admin" && target.status === "active" && (change.kind === "deactivate" || (change.kind === "change_role" && change.role !== "admin"));
    if (losesAdmin && activeAdmins() <= 1) throw new TeamError("last_admin");
    if (change.kind === "change_role" && change.role === target.role) throw new TeamError("same_role");
    if (change.kind === "deactivate" && target.status === "disabled") throw new TeamError("already_disabled");
    if (change.kind === "reactivate" && target.status !== "disabled") throw new TeamError("not_disabled");
  }

  const team = (): Team => ({
    members: members.map((m) => ({ ...m })),
    changes: changes.filter((c) => c.status === "proposed").map((c) => ({ ...c })),
    roles: ROLES.map((role) => ({ role, permissions: STUB_ROLE_PERMISSIONS[role] })),
    canChange: true,
  });

  return {
    load: () => Promise.resolve(team()),
    propose(input: NewChange) {
      if (!input.reason.trim()) return Promise.reject(new TeamError("invalid"));
      if (input.kind === "invite") {
        if (!input.name.trim() || !/^[^@\s]+@[^@\s]+$/.test(input.email)) return Promise.reject(new TeamError("invalid"));
        if (members.some((m) => m.email === input.email.toLowerCase())) return Promise.reject(new TeamError("email_taken"));
      }
      const staffId = input.kind === "invite" ? `sample-new-${count + 1}` : input.staffId;
      if (changes.some((c) => c.status === "proposed" && c.staffId === staffId)) return Promise.reject(new TeamError("waiting"));
      const role = input.kind === "invite" || input.kind === "change_role" ? input.role : null;
      try {
        check({ kind: input.kind, staffId, role }, me.id);
      } catch (error) {
        return Promise.reject(error);
      }
      const target = members.find((m) => m.id === staffId);
      const change: Change = {
        id: nextId(),
        kind: input.kind,
        staffId,
        staffName: input.kind === "invite" ? input.name.trim() : (target?.name ?? ""),
        email: input.kind === "invite" ? input.email.toLowerCase() : null,
        role,
        currentRole: target?.role ?? null,
        reason: input.reason.trim(),
        proposedBy: me.id,
        proposedByName: me.name,
        proposedAt: stamp(),
        status: "proposed",
        yours: true,
        aboutYou: false,
      };
      changes.push(change);
      return Promise.resolve({ ...change });
    },
    syncAccess(staffId: string) {
      const target = members.find((m) => m.id === staffId);
      if (!target) return Promise.reject(new TeamError("not_found"));
      // The sample worker answers at once.
      const access: AccessSync = { state: target.status === "disabled" ? "removed" : "in_access", errorCode: null, requestedAt: stamp(), syncedAt: stamp() };
      target.access = access;
      return Promise.resolve({ ...access, state: "waiting", syncedAt: null });
    },
    decide(changeId: string, decision: Decision, reason: string) {
      const change = changes.find((c) => c.id === changeId && c.status === "proposed");
      if (!change) return Promise.reject(new TeamError("not_found"));
      if (!reason.trim()) return Promise.reject(new TeamError("invalid"));
      if (change.yours !== (decision === "withdrawn") || change.aboutYou) return Promise.reject(new TeamError("same_person"));
      if (decision === "approved") {
        try {
          check(change, me.id);
        } catch (error) {
          return Promise.reject(error);
        }
        const target = members.find((m) => m.id === change.staffId);
        if (change.kind === "invite") {
          members.push(member(change.staffId, change.staffName, change.email ?? "", change.role ?? "support", stamp(), null, {
            passkey: false,
            authenticator: false,
            status: "invited",
          }));
        } else if (target) {
          if (change.kind === "change_role" && change.role) target.role = change.role;
          if (change.kind === "deactivate") target.status = "disabled";
          if (change.kind === "reactivate") target.status = "active";
          if (change.kind === "reset_sign_in") {
            target.hasPasskey = false;
            target.hasAuthenticator = false;
          }
          if (change.kind !== "change_role") {
            target.access = { state: target.status === "disabled" ? "removed" : "in_access", errorCode: null, requestedAt: stamp(), syncedAt: stamp() };
          }
        }
      }
      change.status = decision;
      return Promise.resolve({ ...change });
    },
  };
}
