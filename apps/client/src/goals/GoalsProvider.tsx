import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { GroupsClient } from "./groups";
import { resolveGoalsClient, resolveGroupsClient } from "./resolveGoals";
import { GoalsClient } from "./types";

const GoalsContext = createContext<GoalsClient | null>(null);
const GroupsContext = createContext<GroupsClient | null>(null);

/** One goals client per signed-in client, so one person's demo goals never show to another. */
export function GoalsProvider({
  children,
  client,
  groups,
}: {
  children: ReactNode;
  client?: GoalsClient;
  groups?: GroupsClient;
}) {
  const { session } = useAuth();
  const phone = session?.phone ?? null;
  const resolved = useMemo(() => client ?? resolveGoalsClient(phone), [client, phone]);
  const resolvedGroups = useMemo(
    () => groups ?? resolveGroupsClient(phone, resolved),
    [groups, phone, resolved],
  );
  return (
    <GoalsContext.Provider value={resolved}>
      <GroupsContext.Provider value={resolvedGroups}>{children}</GroupsContext.Provider>
    </GoalsContext.Provider>
  );
}

export function useGoals(): GoalsClient {
  const value = useContext(GoalsContext);
  if (!value) {
    throw new Error("useGoals must be used inside GoalsProvider");
  }
  return value;
}

export function useGroups(): GroupsClient {
  const value = useContext(GroupsContext);
  if (!value) {
    throw new Error("useGroups must be used inside GoalsProvider");
  }
  return value;
}
