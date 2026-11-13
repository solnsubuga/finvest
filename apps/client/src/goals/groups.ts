/**
 * The contract between the group goal screens and the API (`/client/goal-groups`,
 * docs/goals.md, "Group goals").
 *
 * A group goal holds no money. Each member's part is a goal of their own, saved
 * into like any goal, and every saving is their own investment. The group only
 * adds the shared target, the names members chose, and the combined value.
 * Members never see one another's savings or amounts.
 */

import { Money } from "@/payments/types";

import { GoalCategory, Standing } from "./types";

export type GroupMember = {
  displayName: string;
  joinedAt: string;
  isYou: boolean;
  isOwner: boolean;
  /** Only for the one who set the group up, for each of the others: what removes them. */
  memberId: string | null;
};

export type Group = {
  id: string;
  name: string;
  category: GoalCategory;
  target: Money;
  /** YYYY-MM-DD, or null for a group with no date. */
  targetDate: string | null;
  status: "active" | "closed";
  createdAt: string;
  isOwner: boolean;
  /** The client's own goal in the group, where they save. */
  ownGoalId: string;
  value: Money;
  /** 0 to 100 */
  percent: number;
  standing: Standing;
  /** The others are counted as of this day. Null while nobody else has been counted yet. */
  othersAsOf: string | null;
  members: GroupMember[];
  /** Only for the one who set the group up, while a link is open. */
  inviteExpiresAt: string | null;
};

export type NewGroup = {
  name: string;
  category: GoalCategory;
  target: Money;
  targetDate: string | null;
  displayName: string;
  ownTarget: Money;
};

export type InvitePreview = {
  groupId: string;
  name: string;
  category: GoalCategory;
  target: Money;
  targetDate: string | null;
  ownerName: string;
  members: number;
  expiresAt: string;
};

export type NewInvite = { code: string; expiresAt: string };

export type GroupErrorCode =
  | "not_found"
  | "link_not_working"
  | "not_added"
  | "full"
  | "too_many_goals"
  | "closed"
  | "owner_cannot_leave"
  | "sooner_than_every_term"
  | "invalid"
  | "key_reused"
  | "offline"
  | "unavailable";

export class GroupError extends Error {
  constructor(readonly code: GroupErrorCode) {
    super(code);
    this.name = "GroupError";
  }
}

export type GroupsClient = {
  listGroups(): Promise<Group[]>;
  getGroup(groupId: string): Promise<Group>;
  createGroup(details: NewGroup, idempotencyKey: string): Promise<Group>;
  /** A new link, replacing any earlier one. The code is shown once. */
  newInvite(groupId: string): Promise<NewInvite>;
  stopInvite(groupId: string): Promise<Group>;
  previewInvite(code: string): Promise<InvitePreview>;
  joinGroup(
    code: string,
    member: { displayName: string; ownTarget: Money },
    idempotencyKey: string,
  ): Promise<Group>;
  leaveGroup(groupId: string): Promise<void>;
  /** Only the one who set the group up. It also stops the open link. */
  removeMember(groupId: string, memberId: string): Promise<Group>;
  closeGroup(groupId: string): Promise<Group>;
};

/** At most this many in a group at once, the one who set it up included. */
export const MAX_MEMBERS = 10;
export const MAX_DISPLAY_NAME = 30;
/** What `secrets.token_urlsafe(16)` makes on the server. */
export const INVITE_CODE = /^[A-Za-z0-9_-]{22}$/;

/** Where an invite link opens the app. The code goes after #, so it never reaches a server log. */
export const JOIN_PATH = "/goals/groups/join";

export function inviteLink(code: string, origin = "https://app.finvestug.com"): string {
  return `${origin}${JOIN_PATH}#${code}`;
}

/** The code from a pasted link or a typed code, or null when it is neither. */
export function readInviteCode(text: string): string | null {
  const trimmed = text.trim();
  const candidate = trimmed.includes("#") ? trimmed.slice(trimmed.lastIndexOf("#") + 1) : trimmed;
  return INVITE_CODE.test(candidate) ? candidate : null;
}

const errorCopy: Record<GroupErrorCode, string> = {
  not_found: "We could not find this group goal.",
  link_not_working:
    "This link is not working. It may have expired after 7 days or been replaced. Ask for a new one.",
  not_added:
    "We could not add you. You may already be in this group goal, it may be full or closed, or you may have 20 open goals.",
  full: `This group goal has ${MAX_MEMBERS} people, which is the most at once.`,
  too_many_goals: "You have 20 open goals, which is the most at once. Close one to join or start another.",
  closed: "This group goal is closed.",
  owner_cannot_leave: "You set this group goal up, so you close it instead of leaving.",
  sooner_than_every_term:
    "That date is too soon. Your money is invested in Treasury bills, and the shortest one takes 91 days.",
  invalid: "Something here is not right. Check it and try again.",
  key_reused: "This was already sent with other details. Go back and start again.",
  offline: "You seem to be offline. Check your connection and try again.",
  unavailable: "Group goals are not available right now. Try again in a moment.",
};

export function describeGroupError(error: unknown): string {
  return error instanceof GroupError ? errorCopy[error.code] : errorCopy.unavailable;
}

/** A refusal means nothing was made. Anything else may have reached us before the answer was lost. */
export function groupOutcomeUnknown(error: unknown): boolean {
  return !(error instanceof GroupError) || error.code === "unavailable" || error.code === "offline";
}

/** "You, Daniel and 2 others" */
export function memberSummary(members: GroupMember[]): string {
  const names = members.map((m, i) => (m.isYou ? (i === 0 ? "You" : "you") : m.displayName));
  if (names.length <= 1) return members[0]?.isYou ? "Just you so far" : (names[0] ?? "");
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  if (names.length === 3) return `${names[0]}, ${names[1]} and ${names[2]}`;
  return `${names[0]}, ${names[1]} and ${names.length - 2} others`;
}
