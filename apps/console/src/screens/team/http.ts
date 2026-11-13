import { ApiError, type Http } from "@/api/http.ts";
import { PERMISSIONS, ROLES, type Permission, type Role } from "@/auth/types.ts";
import { TeamError, type Change, type Member, type NewChange, type Team, type TeamApi, type TeamErrorCode } from "./api.ts";

interface MemberJson {
  id: string;
  name: string;
  email: string;
  role: Role;
  status: Member["status"];
  has_passkey: boolean;
  has_authenticator: boolean;
  created_at: string;
  last_signed_in_at: string | null;
}

interface ChangeJson {
  id: string;
  kind: Change["kind"];
  staff_id: string;
  staff_name: string;
  email: string | null;
  role: Role | null;
  current_role: Role | null;
  reason: string;
  proposed_by: string;
  proposed_by_name: string;
  proposed_at: string;
  status: Change["status"];
  yours: boolean;
  about_you: boolean;
}

interface TeamJson {
  members: MemberJson[];
  changes: ChangeJson[];
  roles: { role: Role; permissions: string[] }[];
  can_change: boolean;
}

const KNOWN = new Set<string>(PERMISSIONS);
const ROLE_SET = new Set<string>(ROLES);

function member(json: MemberJson): Member {
  return {
    id: json.id,
    name: json.name,
    email: json.email,
    role: json.role,
    status: json.status,
    hasPasskey: json.has_passkey,
    hasAuthenticator: json.has_authenticator,
    createdAt: json.created_at,
    lastSignedInAt: json.last_signed_in_at,
  };
}

export function toChange(json: ChangeJson): Change {
  return {
    id: json.id,
    kind: json.kind,
    staffId: json.staff_id,
    staffName: json.staff_name,
    email: json.email,
    role: json.role,
    currentRole: json.current_role,
    reason: json.reason,
    proposedBy: json.proposed_by,
    proposedByName: json.proposed_by_name,
    proposedAt: json.proposed_at,
    status: json.status,
    yours: json.yours,
    aboutYou: json.about_you,
  };
}

export function toTeam(json: TeamJson): Team {
  return {
    members: json.members.map(member),
    changes: json.changes.map(toChange),
    // A role or permission this console does not know yet is left out rather than shown wrong.
    roles: json.roles
      .filter((entry) => ROLE_SET.has(entry.role))
      .map((entry) => ({
        role: entry.role,
        permissions: entry.permissions.filter((permission): permission is Permission => KNOWN.has(permission)),
      })),
    canChange: json.can_change,
  };
}

const FIELD_CODES: Record<string, TeamErrorCode> = {
  own_account: "own_account",
  same_role: "same_role",
  last_admin: "last_admin",
  already_disabled: "already_disabled",
  not_disabled: "not_disabled",
  taken: "email_taken",
};

/** `deciding` is true for a decision, where a bare refusal means the proposer tried to decide. */
export function toTeamError(error: unknown, deciding = false): TeamError {
  if (!(error instanceof ApiError)) return new TeamError("unavailable");
  const field = error.fieldCode;
  if (field && field in FIELD_CODES) return new TeamError(FIELD_CODES[field] ?? "invalid");
  switch (error.code) {
    case "validation_failed":
      return new TeamError("invalid");
    case "conflict":
      return new TeamError("waiting");
    case "forbidden":
      return new TeamError(deciding ? "same_person" : "forbidden");
    case "not_found":
      return new TeamError("not_found");
    default:
      return new TeamError("unavailable");
  }
}

function body(change: NewChange): Record<string, unknown> {
  switch (change.kind) {
    case "invite":
      return { kind: "invite", name: change.name, email: change.email, role: change.role, reason: change.reason };
    case "change_role":
      return { kind: "change_role", staff_id: change.staffId, role: change.role, reason: change.reason };
    default:
      return { kind: change.kind, staff_id: change.staffId, reason: change.reason };
  }
}

export function createHttpTeamApi(http: Http): TeamApi {
  const call = async <T>(run: () => Promise<T>, deciding = false): Promise<T> => {
    try {
      return await run();
    } catch (error) {
      throw toTeamError(error, deciding);
    }
  };
  return {
    load: () => call(async () => toTeam(await http.request<TeamJson>("GET", "/staff/team"))),
    propose: (change) => call(async () => toChange(await http.request<ChangeJson>("POST", "/staff/team/changes", body(change)))),
    decide: (changeId, decision, reason) =>
      call(async () =>
        toChange(await http.request<ChangeJson>("POST", `/staff/team/changes/${encodeURIComponent(changeId)}/decision`, { decision, reason })),
      true,
      ),
  };
}
