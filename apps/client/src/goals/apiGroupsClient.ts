import type { GroupView, InvitePreview as InviteView } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";
import { Money } from "@/payments/types";

import { Group, GroupError, GroupsClient, InvitePreview } from "./groups";

/**
 * The group goal screens' contract, answered by the real API
 * (`/client/goal-groups`, backend/app/modules/goals/groups.py). Invite codes
 * travel only in request bodies, never in a path.
 */

function money(minor: number, currency: Money["currency"]): Money {
  return { minor, currency };
}

export function toGroup(view: GroupView): Group {
  return {
    id: view.group_id,
    name: view.name,
    category: view.category,
    target: money(view.target_minor, view.currency),
    targetDate: view.target_date,
    status: view.status,
    createdAt: view.created_at,
    isOwner: view.is_owner,
    ownGoalId: view.own_goal_id,
    value: money(view.progress.value_minor, view.currency),
    percent: view.progress.percent,
    standing: view.progress.standing,
    othersAsOf: view.others_as_of,
    members: view.members.map((m) => ({
      displayName: m.display_name,
      joinedAt: m.joined_at,
      isYou: m.is_you,
      isOwner: m.is_owner,
      memberId: m.member_id,
    })),
    inviteExpiresAt: view.invite_expires_at,
  };
}

export function toInvite(view: InviteView): InvitePreview {
  return {
    groupId: view.group_id,
    name: view.name,
    category: view.category,
    target: money(view.target_minor, view.currency),
    targetDate: view.target_date,
    ownerName: view.owner_name,
    members: view.members,
    expiresAt: view.expires_at,
  };
}

/** Which call failed, where the same refusal means different things. */
type Call = "create" | "join" | "invite" | "leave" | "other";

export function toGroupError(error: unknown, call: Call = "other"): GroupError {
  if (error instanceof GroupError) return error;
  if (!(error instanceof ApiError)) return new GroupError("unavailable");
  switch (error.code) {
    case "not_found":
      // An invite that is wrong, replaced, expired or for a closed group is not found, and the
      // API never says which.
      return new GroupError(call === "join" ? "link_not_working" : "not_found");
    case "idempotency_key_reused":
      return new GroupError("key_reused");
    case "conflict":
      if (call === "create") return new GroupError("too_many_goals");
      // Already in it, full, closed, or 20 open goals: the API does not say which, so neither
      // does the app.
      if (call === "join") return new GroupError("not_added");
      if (call === "invite") return new GroupError("full");
      if (call === "leave") return new GroupError("owner_cannot_leave");
      return new GroupError("closed");
    case "validation_failed": {
      const codes = error.fields.map((field) => field.code);
      if (codes.includes("sooner_than_every_term")) return new GroupError("sooner_than_every_term");
      return new GroupError("invalid");
    }
    case "offline":
      return new GroupError("offline");
    default:
      return new GroupError("unavailable");
  }
}

async function wrap<T>(work: () => Promise<T>, call: Call = "other"): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toGroupError(error, call);
  }
}

export function createApiGroupsClient(): GroupsClient {
  return {
    listGroups: () => wrap(async () => (await api("GET /client/goal-groups", {})).groups.map(toGroup)),

    getGroup: (groupId) =>
      wrap(async () => toGroup(await api("GET /client/goal-groups/{group_id}", { params: { group_id: groupId } }))),

    createGroup: (details, idempotencyKey) =>
      wrap(
        async () =>
          toGroup(
            await api("POST /client/goal-groups", {
              body: {
                name: details.name,
                category: details.category,
                target_minor: details.target.minor,
                currency: details.target.currency,
                target_date: details.targetDate,
                display_name: details.displayName,
                own_target_minor: details.ownTarget.minor,
              },
              idempotencyKey,
            }),
          ),
        "create",
      ),

    newInvite: (groupId) =>
      wrap(async () => {
        const view = await api("POST /client/goal-groups/{group_id}/invite", { params: { group_id: groupId } });
        return { code: view.code, expiresAt: view.expires_at };
      }, "invite"),

    stopInvite: (groupId) =>
      wrap(async () =>
        toGroup(await api("DELETE /client/goal-groups/{group_id}/invite", { params: { group_id: groupId } })),
      ),

    previewInvite: (code) =>
      wrap(async () => toInvite(await api("POST /client/goal-groups/invites/preview", { body: { code } })), "join"),

    joinGroup: (code, member, idempotencyKey) =>
      wrap(
        async () =>
          toGroup(
            await api("POST /client/goal-groups/join", {
              body: {
                code,
                display_name: member.displayName,
                own_target_minor: member.ownTarget.minor,
                currency: member.ownTarget.currency,
              },
              idempotencyKey,
            }),
          ),
        "join",
      ),

    leaveGroup: (groupId) =>
      wrap(async () => {
        await api("POST /client/goal-groups/{group_id}/leave", { params: { group_id: groupId } });
      }, "leave"),

    removeMember: (groupId, memberId) =>
      wrap(async () =>
        toGroup(
          await api("POST /client/goal-groups/{group_id}/members/{member_id}/remove", {
            params: { group_id: groupId, member_id: memberId },
          }),
        ),
      ),

    closeGroup: (groupId) =>
      wrap(async () => toGroup(await api("POST /client/goal-groups/{group_id}/close", { params: { group_id: groupId } }))),
  };
}
