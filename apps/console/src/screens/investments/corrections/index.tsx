import { useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { notConnectedCorrectionsApi, type CorrectionsApi } from "./api.ts";
import { CorrectionsSection } from "./Corrections.tsx";
import { createHttpCorrectionsApi } from "./http.ts";
import { createStubCorrectionsApi } from "./stub.ts";

/** The corrections section of the investments page, over the real API or the sample. */
export function CorrectionsPanel() {
  const { session } = useAuth();
  const [{ api, directory }] = useState<{ api: CorrectionsApi; directory: Directory }>(() => {
    if (isStubAuth() && session) {
      // Under the stub, `?sample=empty` shows a new system and `?sample=offline` one that cannot reach Finvest.
      const sample = new URLSearchParams(window.location.search).get("sample");
      const directory = createStubDirectory({ id: session.staffId, name: session.name });
      if (sample === "offline") return { api: notConnectedCorrectionsApi, directory };
      return { api: createStubCorrectionsApi(session.staffId, sample === "empty" ? "empty" : "default"), directory };
    }
    const base = apiBaseUrl();
    if (!base) return { api: notConnectedCorrectionsApi, directory: emptyDirectory };
    const http = createHttp(base);
    return { api: createHttpCorrectionsApi(http), directory: createHttpDirectory(http) };
  });
  if (!session) return null;
  return (
    <CorrectionsSection api={api} directory={directory} me={session.staffId} canChange={can(session, "investment_corrections.change")} />
  );
}
