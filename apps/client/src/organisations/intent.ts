import * as SecureStore from "expo-secure-store";

import { OrganisationIntent, parseIntent } from "./intentTypes";

/** On a phone the choice sits in the app's own secure store, the one store the app has. */
const KEY = "finvest.opening-organisation";

export async function readIntent(): Promise<OrganisationIntent | null> {
  try {
    return parseIntent(await SecureStore.getItemAsync(KEY));
  } catch {
    return null;
  }
}

export async function writeIntent(phone: string): Promise<void> {
  try {
    await SecureStore.setItemAsync(KEY, JSON.stringify({ phone }));
  } catch {
    // Without it Profile, Company and group accounts still opens the application.
  }
}

export async function clearIntent(): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(KEY);
  } catch {
    // Nothing depends on it being gone: Home stops asking once an application exists.
  }
}
