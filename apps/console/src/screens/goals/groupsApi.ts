import { GoalsError, type GoalCategory, type Standing } from "./api.ts";

/**
 * The console's view of group goals.
 *
 * The shapes follow the staff routes GET /staff/goal-groups and
 * GET /staff/goal-groups/{id} in backend/app/modules/goals/api.py (see the
 * "Group goals" section of docs/goals.md). Like GoalsApi, a stub stands in until
 * the console is connected, and the screen does not change when it is.
 *
 * A group holds no money. Each member saves into their own goal, so the screen
 * only reads: nothing here changes a group or anyone's goal.
 */

export type GroupStatus = "active" | "closed";

export interface Group {
  id: string;
  /** The client who set the group up. */
  ownerClientId: string;
  name: string;
  category: GoalCategory;
  /** Shillings, as a whole-number string. The target for every member together. */
  target: string;
  /** YYYY-MM-DD, or null for a group with no date. */
  targetDate: string | null;
  status: GroupStatus;
  /** YYYY-MM-DD in Kampala. */
  createdOn: string;
  closedOn: string | null;
}

export interface GroupMember {
  /** The member's own goal in the group. It opens on the Goals view. */
  goalId: string;
  clientId: string;
  /** The name the member chose for the others to see. */
  displayName: string;
  /** YYYY-MM-DD in Kampala. */
  joinedOn: string;
  leftOn: string | null;
  /** What the member's goal was worth after tax on `valueOn`, as the worker last counted it. */
  value: string;
  /** Null until the worker's first count. */
  valueOn: string | null;
}

export interface GroupProgress {
  /** Every current member's goal together, after tax, as the worker last counted them. */
  value: string;
  percent: number;
  standing: Standing;
}

export interface GroupDetail {
  group: Group;
  progress: GroupProgress;
  /** Current members first by when they joined, then those who have left. */
  members: readonly GroupMember[];
}

export interface GroupsApi {
  /** Groups newest first, open or closed or both. */
  listGroups(status: GroupStatus | null): Promise<readonly Group[]>;
  getGroup(groupId: string): Promise<GroupDetail>;
}

export function groupsErrorMessage(error: unknown): string {
  if (error instanceof GoalsError) {
    switch (error.code) {
      case "not_connected":
        return "The console is not connected to Finvest yet.";
      case "forbidden":
        return "Your role cannot see goals.";
      case "not_found":
        return "This group goal could not be found.";
      case "unavailable":
        break;
    }
  }
  return "We could not reach Finvest. Try again in a moment.";
}

/** Until the API client exists, a normal build has nothing to show. */
export const notConnectedGroupsApi: GroupsApi = {
  listGroups: () => Promise.reject(new GoalsError("not_connected")),
  getGroup: () => Promise.reject(new GoalsError("not_connected")),
};
