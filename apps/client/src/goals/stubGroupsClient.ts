import { Money } from "@/payments/types";

import { addMonths } from "./dates";
import { Group, GroupError, GroupMember, GroupsClient, INVITE_CODE, InvitePreview, MAX_MEMBERS, NewGroup } from "./groups";
import { GoalCategory, GoalsClient, Standing } from "./types";

/**
 * An in-memory stand-in for the group goals API, for tests and demo builds.
 * `resolveGoals.ts` keeps it out of production builds. Nothing is real and
 * everything is forgotten when the app reloads.
 *
 * The client's own part is a real goal in the demo goals client, so saving to
 * it works end to end. The other members are pretend people whose figures are
 * fixed, as if the worker had counted them yesterday.
 */

/** Opens the pretend group "Wedding in Mbarara" in the demo. */
export const DEMO_INVITE_CODE = "DemoInviteWeddingMbara";

type Other = { displayName: string; joinedAt: string; value: number; isOwner: boolean };

type StoredGroup = {
  id: string;
  details: Omit<NewGroup, "displayName" | "ownTarget">;
  createdAt: string;
  createdOn: string;
  status: "active" | "closed";
  isOwner: boolean;
  ownGoalId: string;
  ownName: string;
  joinedAt: string;
  others: Other[];
  invite: { code: string; expiresAt: string } | null;
};

type StubOptions = {
  goals: GoalsClient;
  delayMs?: number;
  now?: () => number;
  /** Start with a family group goal already set up. */
  seeded?: boolean;
};

const KAMPALA_MS = 3 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const ugx = (minor: number): Money => ({ currency: "UGX", minor });

function kampalaDay(ms: number): string {
  return new Date(ms + KAMPALA_MS).toISOString().slice(0, 10);
}

function standing(target: number, value: number, setUpOn: string, targetDate: string | null, today: string): Standing {
  if (value >= target) return "reached";
  if (!targetDate) return "no_date";
  const span = Math.max(1, (Date.parse(targetDate) - Date.parse(setUpOn)) / DAY_MS);
  const elapsed = Math.min(Math.max(0, (Date.parse(today) - Date.parse(setUpOn)) / DAY_MS), span);
  // Integer arithmetic, rounded up, as the server does it.
  const expected = Math.ceil((target * elapsed) / span);
  return value >= expected ? "on_track" : "behind";
}

export function createStubGroupsClient(options: StubOptions): GroupsClient {
  const { goals } = options;
  const delayMs = options.delayMs ?? 400;
  const now = options.now ?? Date.now;
  const groups = new Map<string, StoredGroup>();
  const byKey = new Map<string, { id: string; body: string }>();
  let counter = 0;
  let seeding: Promise<void> | null = null;

  const pause = () =>
    delayMs > 0 ? new Promise<void>((resolve) => setTimeout(resolve, delayMs)) : Promise.resolve();
  const today = () => kampalaDay(now());
  const nextId = (prefix: string) => `demo-${prefix}-${++counter}`;

  function randomCode(): string {
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let code = "";
    for (let i = 0; i < 22; i += 1) code += alphabet[Math.floor(Math.random() * alphabet.length)];
    return code;
  }

  async function ownGoal(name: string, category: GoalCategory, target: Money, targetDate: string | null, key: string) {
    return goals.createGoal({ name, category, target, targetDate }, key);
  }

  async function seed(): Promise<void> {
    if (!options.seeded) return;
    seeding ??= (async () => {
      const createdOn = kampalaDay(now() - 90 * DAY_MS);
      const targetDate = addMonths(today(), 21);
      const goal = await ownGoal("Family land in Mukono", "land_or_home", ugx(4_000_000), targetDate, "demo-group-seed");
      groups.set("demo-group-seed", {
        id: "demo-group-seed",
        details: { name: "Family land in Mukono", category: "land_or_home", target: ugx(12_000_000), targetDate },
        createdAt: `${createdOn}T07:00:00Z`,
        createdOn,
        status: "active",
        isOwner: true,
        ownGoalId: goal.id,
        ownName: "Amara",
        joinedAt: `${createdOn}T07:00:00Z`,
        others: [
          { displayName: "Daniel", joinedAt: `${createdOn}T10:12:00Z`, value: 1_850_000, isOwner: false },
          { displayName: "Miriam", joinedAt: `${addMonths(createdOn, 1)}T16:40:00Z`, value: 950_000, isOwner: false },
        ],
        invite: null,
      });
    })();
    await seeding;
  }

  async function view(stored: StoredGroup): Promise<Group> {
    const own = await goals.getGoal(stored.ownGoalId);
    const target = stored.details.target.minor;
    const value = own.progress.value.minor + stored.others.reduce((sum, o) => sum + o.value, 0);
    const you: GroupMember = {
      displayName: stored.ownName,
      joinedAt: stored.joinedAt,
      isYou: true,
      isOwner: stored.isOwner,
    };
    const others: GroupMember[] = stored.others.map((o) => ({
      displayName: o.displayName,
      joinedAt: o.joinedAt,
      isYou: false,
      isOwner: o.isOwner,
    }));
    const members = [you, ...others].sort((a, b) => a.joinedAt.localeCompare(b.joinedAt));
    const open = stored.invite && Date.parse(stored.invite.expiresAt) > now() ? stored.invite : null;
    return {
      id: stored.id,
      name: stored.details.name,
      category: stored.details.category,
      target: stored.details.target,
      targetDate: stored.details.targetDate,
      status: stored.status,
      createdAt: stored.createdAt,
      isOwner: stored.isOwner,
      ownGoalId: stored.ownGoalId,
      value: ugx(value),
      percent: Math.min(100, Math.floor((value * 100) / target)),
      standing: standing(target, value, stored.createdOn, stored.details.targetDate, today()),
      othersAsOf: stored.others.length > 0 ? kampalaDay(now() - DAY_MS) : null,
      members,
      inviteExpiresAt: stored.isOwner && open ? open.expiresAt : null,
    };
  }

  function find(groupId: string): StoredGroup {
    const stored = groups.get(groupId);
    if (!stored) throw new GroupError("not_found");
    return stored;
  }

  function owned(groupId: string): StoredGroup {
    const stored = find(groupId);
    if (!stored.isOwner) throw new GroupError("not_found");
    if (stored.status !== "active") throw new GroupError("closed");
    return stored;
  }

  function demoInvite(): InvitePreview {
    return {
      groupId: "demo-group-wedding",
      name: "Wedding in Mbarara",
      category: "wedding",
      target: ugx(20_000_000),
      targetDate: addMonths(today(), 10),
      ownerName: "Aunt Ruth",
      members: 3,
      expiresAt: new Date(now() + 5 * DAY_MS).toISOString(),
    };
  }

  function lookUp(code: string): InvitePreview {
    if (!INVITE_CODE.test(code)) throw new GroupError("link_not_working");
    if (code === DEMO_INVITE_CODE) return demoInvite();
    for (const stored of groups.values()) {
      if (stored.invite?.code === code && stored.status === "active" && Date.parse(stored.invite.expiresAt) > now()) {
        return {
          groupId: stored.id,
          name: stored.details.name,
          category: stored.details.category,
          target: stored.details.target,
          targetDate: stored.details.targetDate,
          ownerName: stored.ownName,
          members: stored.others.length + 1,
          expiresAt: stored.invite.expiresAt,
        };
      }
    }
    throw new GroupError("link_not_working");
  }

  function remembered(key: string, body: string): string | null {
    const known = byKey.get(key);
    if (!known) return null;
    if (known.body !== body) throw new GroupError("key_reused");
    return known.id;
  }

  return {
    async listGroups() {
      await seed();
      await pause();
      const listed = [...groups.values()].sort(
        (a, b) => a.status.localeCompare(b.status) || b.joinedAt.localeCompare(a.joinedAt),
      );
      return Promise.all(listed.map(view));
    },

    async getGroup(groupId) {
      await seed();
      await pause();
      return view(find(groupId));
    },

    async createGroup(details, idempotencyKey) {
      await pause();
      const body = JSON.stringify(details);
      const known = remembered(idempotencyKey, body);
      if (known) return view(find(known));
      const goal = await ownGoal(details.name, details.category, details.ownTarget, details.targetDate, `group-${idempotencyKey}`);
      const id = nextId("group");
      const at = new Date(now()).toISOString();
      groups.set(id, {
        id,
        details: { name: details.name, category: details.category, target: details.target, targetDate: details.targetDate },
        createdAt: at,
        createdOn: today(),
        status: "active",
        isOwner: true,
        ownGoalId: goal.id,
        ownName: details.displayName,
        joinedAt: at,
        others: [],
        invite: null,
      });
      byKey.set(idempotencyKey, { id, body });
      return view(find(id));
    },

    async newInvite(groupId) {
      await pause();
      const stored = owned(groupId);
      if (stored.others.length + 1 >= MAX_MEMBERS) throw new GroupError("full");
      stored.invite = { code: randomCode(), expiresAt: new Date(now() + 7 * DAY_MS).toISOString() };
      return { ...stored.invite };
    },

    async stopInvite(groupId) {
      await pause();
      const stored = owned(groupId);
      stored.invite = null;
      return view(stored);
    },

    async previewInvite(code) {
      await seed();
      await pause();
      return lookUp(code);
    },

    async joinGroup(code, member, idempotencyKey) {
      await seed();
      await pause();
      const body = JSON.stringify({ code, member });
      const known = remembered(idempotencyKey, body);
      if (known) return view(find(known));
      const invite = lookUp(code);
      if (groups.has(invite.groupId)) throw new GroupError("already_in");
      if (invite.members >= MAX_MEMBERS) throw new GroupError("full");
      const goal = await ownGoal(invite.name, invite.category, member.ownTarget, invite.targetDate, `join-${idempotencyKey}`);
      const at = new Date(now()).toISOString();
      const createdOn = kampalaDay(now() - 60 * DAY_MS);
      groups.set(invite.groupId, {
        id: invite.groupId,
        details: { name: invite.name, category: invite.category, target: invite.target, targetDate: invite.targetDate },
        createdAt: `${createdOn}T08:00:00Z`,
        createdOn,
        status: "active",
        isOwner: false,
        ownGoalId: goal.id,
        ownName: member.displayName,
        joinedAt: at,
        others: [
          { displayName: invite.ownerName, joinedAt: `${createdOn}T08:00:00Z`, value: 6_400_000, isOwner: true },
          { displayName: "Brian", joinedAt: `${createdOn}T12:30:00Z`, value: 2_100_000, isOwner: false },
          { displayName: "Esther", joinedAt: `${addMonths(createdOn, 1)}T09:05:00Z`, value: 1_500_000, isOwner: false },
        ].slice(0, invite.members),
        invite: null,
      });
      byKey.set(idempotencyKey, { id: invite.groupId, body });
      return view(find(invite.groupId));
    },

    async leaveGroup(groupId) {
      await pause();
      const stored = find(groupId);
      if (stored.isOwner && stored.status === "active") throw new GroupError("owner_cannot_leave");
      groups.delete(groupId);
    },

    async closeGroup(groupId) {
      await pause();
      const stored = find(groupId);
      if (!stored.isOwner) throw new GroupError("not_found");
      stored.status = "closed";
      stored.invite = null;
      return view(stored);
    },
  };
}
