/**
 * The console's view of the team: who works here, what each role may do, and
 * the changes waiting for a second admin. The shapes follow GET /staff/team
 * and its change routes (docs/console-admin.md).
 */
import type { Permission, Role } from "@/auth/types.ts";

export type StaffStatus = "invited" | "active" | "disabled";
export type ChangeKind = "invite" | "change_role" | "deactivate" | "reactivate" | "reset_sign_in";
export type Decision = "approved" | "rejected" | "withdrawn";

export interface Member {
  id: string;
  name: string;
  email: string;
  role: Role;
  status: StaffStatus;
  hasPasskey: boolean;
  hasAuthenticator: boolean;
  createdAt: string;
  lastSignedInAt: string | null;
}

export interface Change {
  id: string;
  kind: ChangeKind;
  staffId: string;
  staffName: string;
  /** Set for an invite. */
  email: string | null;
  /** The new role, for an invite or a role change. */
  role: Role | null;
  /** The role the person holds now. Null for an invite. */
  currentRole: Role | null;
  reason: string;
  proposedBy: string;
  proposedByName: string;
  proposedAt: string;
  status: "proposed" | Decision;
  /** The signed-in admin proposed it: they can withdraw it, never approve it. */
  yours: boolean;
  /** It is about the signed-in admin, who therefore cannot decide it. */
  aboutYou: boolean;
}

export interface RolePermissions {
  role: Role;
  permissions: readonly Permission[];
}

export interface Team {
  members: Member[];
  changes: Change[];
  roles: RolePermissions[];
  /** `team.change`: may propose and decide. The API checks it again. */
  canChange: boolean;
}

export type NewChange =
  | { kind: "invite"; name: string; email: string; role: Role; reason: string }
  | { kind: "change_role"; staffId: string; role: Role; reason: string }
  | { kind: "deactivate" | "reactivate" | "reset_sign_in"; staffId: string; reason: string };

export interface TeamApi {
  load(): Promise<Team>;
  propose(change: NewChange): Promise<Change>;
  decide(changeId: string, decision: Decision, reason: string): Promise<Change>;
}

export type TeamErrorCode =
  | "invalid"
  | "own_account"
  | "same_role"
  | "last_admin"
  | "already_disabled"
  | "not_disabled"
  | "waiting"
  | "email_taken"
  | "same_person"
  | "forbidden"
  | "not_found"
  | "not_connected"
  | "unavailable";

export class TeamError extends Error {
  constructor(readonly code: TeamErrorCode) {
    super(code);
    this.name = "TeamError";
  }
}

const MESSAGES: Record<TeamErrorCode, string> = {
  invalid: "Finvest refused this change. Check the name, email address and reason.",
  own_account: "Nobody changes their own account. Another admin must propose it.",
  same_role: "They already hold that role.",
  last_admin: "This would leave Finvest with no active admin, so it is refused.",
  already_disabled: "This person is already deactivated.",
  not_disabled: "This person is not deactivated.",
  email_taken: "Someone on the team already uses this email address.",
  waiting: "A change for this person is already waiting for a second admin.",
  same_person: "A change needs a second admin. Someone other than the proposer must decide it.",
  forbidden: "Your role can see the team but not change it.",
  not_found: "This change has already been decided.",
  not_connected: "The team is not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function teamErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof TeamError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new TeamError("not_connected"));

export const notConnectedTeamApi: TeamApi = { load: notConnected, propose: notConnected, decide: notConnected };
