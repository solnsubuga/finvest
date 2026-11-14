import { addDays } from "@/screens/automatic-investing/labels.ts";
import { GoalsError } from "./api.ts";
import type { GroupDetail, GroupMember, GroupsApi, GroupStatus } from "./groupsApi.ts";
import type { StubCase } from "./stub.ts";

/**
 * Sample group goals for `pnpm dev` and previews. Not real clients and not
 * connected to Finvest. Days are counted from the day asked for.
 */

interface SampleMember extends Omit<GroupMember, "joinedOn" | "leftOn" | "valueOn"> {
  joinedDaysAgo: number;
  leftDaysAgo?: number;
  /** Days since the worker last counted this member, or null if it has not yet. */
  countedDaysAgo: number | null;
}

interface Sample {
  group: Omit<GroupDetail["group"], "id" | "createdOn" | "closedOn" | "targetDate"> & {
    createdDaysAgo: number;
    closedDaysAgo?: number;
    dueInDays: number | null;
  };
  progress: GroupDetail["progress"];
  members: readonly SampleMember[];
}

const SAMPLES: readonly Sample[] = [
  {
    group: { ownerClientId: "client-5", name: "Family land in Mukono", category: "land_or_home", currency: "UGX", target: "12000000", status: "active", createdDaysAgo: 89, dueInDays: 640 },
    progress: { value: "2800000", percent: 23, standing: "on_track" },
    members: [
      { goalId: "sample-group-goal-1", clientId: "client-5", displayName: "Amara", value: "0", joinedDaysAgo: 89, countedDaysAgo: 1 },
      { goalId: "sample-group-goal-2", clientId: "client-6", displayName: "Daniel", value: "1850000", joinedDaysAgo: 89, countedDaysAgo: 1 },
      { goalId: "sample-group-goal-3", clientId: "client-7", displayName: "Miriam", value: "950000", joinedDaysAgo: 58, countedDaysAgo: 1 },
      { goalId: "sample-group-goal-4", clientId: "client-8", displayName: "Joel", value: "400000", joinedDaysAgo: 50, leftDaysAgo: 12, countedDaysAgo: 13 },
    ],
  },
  {
    group: { ownerClientId: "client-9", name: "Wedding in Mbarara", category: "wedding", currency: "UGX", target: "15000000", status: "active", createdDaysAgo: 40, dueInDays: 230 },
    progress: { value: "10000000", percent: 66, standing: "on_track" },
    members: [
      { goalId: "sample-group-goal-5", clientId: "client-9", displayName: "Patience", value: "6400000", joinedDaysAgo: 40, countedDaysAgo: 1 },
      { goalId: "sample-group-goal-6", clientId: "client-10", displayName: "Brian", value: "2100000", joinedDaysAgo: 39, countedDaysAgo: 1 },
      { goalId: "sample-group-goal-7", clientId: "client-11", displayName: "Esther", value: "1500000", joinedDaysAgo: 9, countedDaysAgo: 1 },
      { goalId: "sample-group-goal-8", clientId: "client-12", displayName: "Ruth", value: "0", joinedDaysAgo: 0, countedDaysAgo: null },
    ],
  },
  {
    group: { ownerClientId: "client-13", name: "Office for our shop", category: "business", currency: "UGX", target: "8000000", status: "closed", createdDaysAgo: 150, closedDaysAgo: 6, dueInDays: null },
    progress: { value: "0", percent: 0, standing: "no_date" },
    members: [
      { goalId: "sample-group-goal-9", clientId: "client-13", displayName: "Ivan", value: "2200000", joinedDaysAgo: 150, leftDaysAgo: 6, countedDaysAgo: 7 },
      { goalId: "sample-group-goal-10", clientId: "client-14", displayName: "Sharon", value: "1300000", joinedDaysAgo: 140, leftDaysAgo: 6, countedDaysAgo: 7 },
    ],
  },
];

function build(sample: Sample, index: number, today: string): GroupDetail {
  const { createdDaysAgo, closedDaysAgo, dueInDays, ...group } = sample.group;
  return {
    group: {
      ...group,
      id: `sample-group-${index + 1}`,
      createdOn: addDays(today, -createdDaysAgo),
      closedOn: closedDaysAgo === undefined ? null : addDays(today, -closedDaysAgo),
      targetDate: dueInDays === null ? null : addDays(today, dueInDays),
    },
    progress: sample.progress,
    members: sample.members.map(({ joinedDaysAgo, leftDaysAgo, countedDaysAgo, ...member }) => ({
      ...member,
      joinedOn: addDays(today, -joinedDaysAgo),
      leftOn: leftDaysAgo === undefined ? null : addDays(today, -leftDaysAgo),
      valueOn: countedDaysAgo === null ? null : addDays(today, -countedDaysAgo),
    })),
  };
}

export function sampleGroups(today: string): readonly GroupDetail[] {
  return SAMPLES.map((sample, index) => build(sample, index, today)).sort((a, b) => b.group.createdOn.localeCompare(a.group.createdOn));
}

export function createStubGroupsApi(today: string, delayMs = 150, which: StubCase = "sample"): GroupsApi {
  const settle = <T,>(value: () => T): Promise<T> =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new Error(String(error)));
        }
      }, delayMs),
    );
  const all = () => {
    if (which === "error") throw new GoalsError("unavailable");
    return which === "empty" ? [] : sampleGroups(today);
  };
  return {
    listGroups: (status: GroupStatus | null) =>
      settle(() => all().map((detail) => detail.group).filter((group) => status === null || group.status === status)),
    getGroup: (groupId) =>
      settle(() => {
        const found = all().find((detail) => detail.group.id === groupId);
        if (!found) throw new GoalsError("not_found");
        return found;
      }),
  };
}
