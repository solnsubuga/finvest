import { useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { notConnectedRunRequestsApi, type RunKind, type RunRequestsApi } from "./api.ts";
import { createHttpRunRequestsApi } from "./http.ts";
import { RunAgain } from "./RunAgain.tsx";
import { createStubRunRequestsApi } from "./stub.ts";

const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

/** Asking for a run again, over the real API or the sample, for the signed-in member of staff. */
export function RunAgainPanel({ kind }: { kind: RunKind }) {
  const { session } = useAuth();
  const [{ api, directory }] = useState<{ api: RunRequestsApi; directory: Directory }>(() => {
    if (isStubAuth() && session) {
      // Under the stub, `?sample=empty` shows a new system and `?sample=offline` one that cannot reach Finvest.
      const sample = new URLSearchParams(window.location.search).get("sample");
      const directory = createStubDirectory({ id: session.staffId, name: session.name });
      if (sample === "offline" || sample === "error") return { api: notConnectedRunRequestsApi, directory };
      return { api: createStubRunRequestsApi(session.staffId, sample === "empty" ? "empty" : "default"), directory };
    }
    const base = apiBaseUrl();
    if (!base) return { api: notConnectedRunRequestsApi, directory: emptyDirectory };
    const http = createHttp(base);
    return { api: createHttpRunRequestsApi(http), directory: createHttpDirectory(http) };
  });
  if (!session || !can(session, "investments.view")) return null;
  return (
    <RunAgain
      api={api}
      directory={directory}
      kind={kind}
      me={session.staffId}
      canChange={can(session, "investment_corrections.change")}
      today={kampalaToday()}
    />
  );
}
