import { useEffect } from "react";

import { useDataSaving } from "@/dataSaving/DataSavingProvider";

import { prefetchRoutes } from "@/navigation/prefetchRoutes";

/** How long after the first screen shows before the other screens' code is fetched. */
export const PREFETCH_DELAY_MS = 1500;

/**
 * Fetches the other screens' code on the web once the first screen is up, unless the client
 * chose light mode (prefetchRoutes.web.ts). Renders nothing.
 */
export function RoutePrefetch() {
  const { ready, light } = useDataSaving();


  useEffect(() => {
    if (!ready || light) {
      return;
    }
    const timer = setTimeout(prefetchRoutes, PREFETCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [ready, light]);

  return null;
}
