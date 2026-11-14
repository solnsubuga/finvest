import { useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { notConnectedReissuesApi, type ReissuesApi } from "./api.ts";
import { createHttpReissuesApi } from "./http.ts";
import { Reissues } from "./Reissues.tsx";
import { createStubReissuesApi } from "./stub.ts";

const kampalaToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Kampala" }).format(new Date());

/** Sending investment notes again, over the real API or the sample, for the signed-in member of staff. */
export function ReissuesPanel() {
  const { session } = useAuth();
  const [{ api, directory }] = useState<{ api: ReissuesApi; directory: Directory }>(() => {
    if (isStubAuth() && session) {
      // Under the stub, `?sample=empty` shows a new system and `?sample=offline` one that cannot reach Finvest.
      const sample = new URLSearchParams(window.location.search).get("sample");
      const directory = createStubDirectory({ id: session.staffId, name: session.name });
      if (sample === "offline" || sample === "error") return { api: notConnectedReissuesApi, directory };
      return { api: createStubReissuesApi(session.staffId, sample === "empty" ? "empty" : "default"), directory };
    }
    const base = apiBaseUrl();
    if (!base) return { api: notConnectedReissuesApi, directory: emptyDirectory };
    const http = createHttp(base);
    return { api: createHttpReissuesApi(http), directory: createHttpDirectory(http) };
  });
  if (!session || !can(session, "investments.view")) return null;
  return <Reissues api={api} directory={directory} me={session.staffId} canChange={can(session, "client_statements.send")} today={kampalaToday()} />;
}
