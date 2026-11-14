/**
 * Data saving: two choices the client makes for this phone or browser only.
 * They change what the app asks for over the network, never anything about
 * the client's money, so they are kept on the device and not on our servers.
 */
export type DataSavingSettings = {
  /** Figures as text instead of charts, and fewer reads in the background. */
  light: boolean;
  /** Hold statement, receipt and certificate downloads while on mobile data. */
  wifiOnlyDocuments: boolean;
};

export const DEFAULT_SETTINGS: DataSavingSettings = { light: false, wifiOnlyDocuments: false };

/** What the device says it is connected through. */
export type Connection = "wifi" | "mobile" | "offline" | "unknown";

/** Reads every key it knows and ignores the rest, so an old or damaged copy falls back to off. */
export function parseSettings(raw: string | null): DataSavingSettings {
  if (!raw) {
    return DEFAULT_SETTINGS;
  }
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) {
      return DEFAULT_SETTINGS;
    }
    const record = value as Record<string, unknown>;
    return {
      light: record.light === true,
      wifiOnlyDocuments: record.wifiOnlyDocuments === true,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}
