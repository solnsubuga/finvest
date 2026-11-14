import { useCallback } from "react";

import { useDataSaving } from "./DataSavingProvider";
import { currentConnection } from "./network";

/**
 * With "Download documents on Wi-Fi only" on, a download asked for on mobile
 * data is held: the screen says so and a second tap downloads anyway. A
 * connection the device cannot name is not held, because we cannot tell.
 */
export function useDownloadGate(): () => Promise<boolean> {
  const { wifiOnlyDocuments } = useDataSaving();
  return useCallback(
    async () => wifiOnlyDocuments && (await currentConnection()) === "mobile",
    [wifiOnlyDocuments],
  );
}

export const HELD_COPY = "You are on mobile data. Wait for Wi-Fi, or tap again to download now.";
