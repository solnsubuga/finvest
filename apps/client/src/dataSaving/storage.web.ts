import { DataSavingSettings, parseSettings } from "./types";

/** In a browser the choices sit in this browser's storage. A private window may refuse it. */
const KEY = "finvest.data-saving";

export async function readSettings(): Promise<DataSavingSettings> {
  try {
    return parseSettings(window.localStorage.getItem(KEY));
  } catch {
    return parseSettings(null);
  }
}

export async function writeSettings(settings: DataSavingSettings): Promise<void> {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(settings));
  } catch {
    // The choice still holds until the page closes.
  }
}
