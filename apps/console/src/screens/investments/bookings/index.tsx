import { useState } from "react";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { useAuth } from "@/auth/context.ts";
import { isStubAuth } from "@/auth/index.ts";
import { can } from "@/auth/types.ts";
import { emptyDirectory, type Directory } from "@/directory/api.ts";
import { createHttpDirectory } from "@/directory/http.ts";
import { createStubDirectory } from "@/directory/stub.ts";
import { createHttpCatalogueApi } from "../../products/http.ts";
import { notConnectedBookingsApi, type BookingsApi } from "./api.ts";
import { Bookings } from "./Bookings.tsx";
import { createHttpBookingsApi } from "./http.ts";
import { createStubBookingsApi } from "./stub.ts";

/** Booking for a client, over the real API or the sample, for the signed-in member of staff. */
export function BookingsPanel() {
  const { session } = useAuth();
  const [{ api, directory }] = useState<{ api: BookingsApi; directory: Directory }>(() => {
    if (isStubAuth() && session) {
      // Under the stub, `?sample=empty` shows a new system and `?sample=offline` one that cannot reach Finvest.
      const sample = new URLSearchParams(window.location.search).get("sample");
      const directory = createStubDirectory({ id: session.staffId, name: session.name });
      if (sample === "offline" || sample === "error") return { api: notConnectedBookingsApi, directory };
      return { api: createStubBookingsApi(session.staffId, sample === "empty" ? "empty" : "default"), directory };
    }
    const base = apiBaseUrl();
    if (!base) return { api: notConnectedBookingsApi, directory: emptyDirectory };
    const http = createHttp(base);
    return { api: createHttpBookingsApi(http, createHttpCatalogueApi(http)), directory: createHttpDirectory(http) };
  });
  if (!session || !can(session, "investments.view")) return null;
  return <Bookings api={api} directory={directory} me={session.staffId} canChange={can(session, "investment_corrections.change")} />;
}
