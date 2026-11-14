import { createContext, ReactNode, useContext, useMemo } from "react";

import { useAuth } from "@/auth/AuthProvider";

import { createApiProfileClient } from "./apiProfileClient";
import { ProfileClient } from "./types";

type StubModule = typeof import("./stubProfileClient");

/** The demo follows the demo sign-in: tests, or EXPO_PUBLIC_DEMO_AUTH=1 on purpose. */
function loadStub(): StubModule | null {
  if (process.env.EXPO_PUBLIC_DEMO_AUTH === "1" || process.env.NODE_ENV === "test") {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("./stubProfileClient") as StubModule;
  }
  return null;
}

export function resolveProfileClient(phone: string | null, stub: StubModule | null = loadStub()): ProfileClient {
  if (stub) {
    return stub.createStubProfileClient(phone);
  }
  return createApiProfileClient();
}

const ProfileContext = createContext<ProfileClient | null>(null);

export function ProfileProvider({ children, client }: { children: ReactNode; client?: ProfileClient }) {
  const { session } = useAuth();
  const phone = session?.phone ?? null;
  // One client per signed-in number, so the demo never shows the last person's details.
  const resolved = useMemo(() => client ?? resolveProfileClient(phone), [client, phone]);
  return <ProfileContext.Provider value={resolved}>{children}</ProfileContext.Provider>;
}

export function useProfile(): ProfileClient {
  const value = useContext(ProfileContext);
  if (!value) {
    throw new Error("useProfile must be used inside ProfileProvider");
  }
  return value;
}
