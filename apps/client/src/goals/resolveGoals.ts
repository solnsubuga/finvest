import { GoalError, GoalsClient } from "./types";

type StubModule = typeof import("./stubGoalsClient");

/**
 * The in-memory demo shows goals and savings that never happened, so it must
 * never be what a real client reaches. It follows the demo sign-in and the
 * demo payments: loaded in development, and in a production build only when
 * EXPO_PUBLIC_DEMO_AUTH=1 was set on purpose. Written inline, so the bundler
 * leaves the stub out of a normal production build.
 */
function loadStub(): StubModule | null {
  if (__DEV__ || process.env.EXPO_PUBLIC_DEMO_AUTH === "1") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubGoalsClient") as StubModule;
  }
  return null;
}

/** The demo account that starts with the canvas's goals. */
export const SEEDED_DEMO_PHONE = "+256772123456";

/** Chooses what the goal screens talk to. Without the stub, every call is refused. */
export function resolveGoalsClient(
  phone: string | null,
  stub: StubModule | null = loadStub(),
): GoalsClient {
  if (stub) {
    return stub.createStubGoalsClient({ seeded: phone === SEEDED_DEMO_PHONE });
  }
  return unavailableGoalsClient;
}

const refuse = async (): Promise<never> => {
  throw new GoalError("unavailable");
};

export const unavailableGoalsClient: GoalsClient = {
  getPlan: refuse,
  createGoal: refuse,
  listGoals: refuse,
  getGoal: refuse,
  closeGoal: refuse,
  saveToGoal: refuse,
};
