import { createApiGoalsClient } from "./apiGoalsClient";
import { createApiGroupsClient } from "./apiGroupsClient";
import { GroupsClient } from "./groups";
import { GoalsClient } from "./types";

type StubModule = typeof import("./stubGoalsClient");
type GroupStubModule = typeof import("./stubGroupsClient");

/**
 * The in-memory demo shows goals and savings that never happened, so it must
 * never be what a real client reaches. It follows the demo sign-in: loaded in
 * tests, and in a build only when EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose.
 * Written inline, so the bundler leaves the stub out of a normal production
 * build.
 */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubGoalsClient") as StubModule;
  }
  return null;
}

/** The demo account that starts with the canvas's goals. */
export const SEEDED_DEMO_PHONE = "+256772123456";

/** Chooses what the goal screens talk to: the demo when it is switched on, otherwise the API. */
export function resolveGoalsClient(
  phone: string | null,
  stub: StubModule | null = loadStub(),
): GoalsClient {
  if (stub) {
    return stub.createStubGoalsClient({ seeded: phone === SEEDED_DEMO_PHONE });
  }
  return createApiGoalsClient();
}

function loadGroupStub(): GroupStubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubGroupsClient") as GroupStubModule;
  }
  return null;
}

/** The same choice for group goals. In the demo a member's own part is a goal in `goals`. */
export function resolveGroupsClient(
  phone: string | null,
  goals: GoalsClient,
  stub: GroupStubModule | null = loadGroupStub(),
): GroupsClient {
  if (stub) {
    return stub.createStubGroupsClient({ goals, seeded: phone === SEEDED_DEMO_PHONE });
  }
  return createApiGroupsClient();
}
