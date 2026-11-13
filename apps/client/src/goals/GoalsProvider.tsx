import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { resolveGoalsClient } from "./resolveGoals";
import { GoalsClient } from "./types";

const GoalsContext = createContext<GoalsClient | null>(null);

/** One goals client per signed-in client, so one person's demo goals never show to another. */
export function GoalsProvider({ children, client }: { children: ReactNode; client?: GoalsClient }) {
  const { session } = useAuth();
  const phone = session?.phone ?? null;
  const resolved = useMemo(() => client ?? resolveGoalsClient(phone), [client, phone]);
  return <GoalsContext.Provider value={resolved}>{children}</GoalsContext.Provider>;
}

export function useGoals(): GoalsClient {
  const value = useContext(GoalsContext);
  if (!value) {
    throw new Error("useGoals must be used inside GoalsProvider");
  }
  return value;
}
