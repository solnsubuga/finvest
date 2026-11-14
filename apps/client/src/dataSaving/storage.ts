import * as SecureStore from "expo-secure-store";

import { DataSavingSettings, parseSettings } from "./types";

/**
 * On a phone the choices sit in the app's own secure store. They are not
 * secret, but it is the one store the app already has, and it keeps them to
 * this device.
 */
const KEY = "finvest.data-saving";

export async function readSettings(): Promise<DataSavingSettings> {
  try {
    return parseSettings(await SecureStore.getItemAsync(KEY));
  } catch {
    return parseSettings(null);
  }
}

export async function writeSettings(settings: DataSavingSettings): Promise<void> {
  try {
    await SecureStore.setItemAsync(KEY, JSON.stringify(settings));
  } catch {
    // The choice still holds until the app closes. Nothing else depends on it.
  }
}
