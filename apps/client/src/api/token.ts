import * as SecureStore from "expo-secure-store";

import { USES_COOKIE } from "./config";

/**
 * The phone app's session token. It lives in the Keychain on iPhone and the
 * Keystore on Android, readable only by this app on this device and never
 * backed up. On the web there is no token: the browser holds an HttpOnly
 * cookie, so nothing here runs.
 */
const KEY = "finvest.session";
const OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

let cached: string | null | undefined;

export async function readToken(): Promise<string | null> {
  if (USES_COOKIE) return null;
  if (cached === undefined) {
    try {
      cached = await SecureStore.getItemAsync(KEY, OPTIONS);
    } catch {
      cached = null;
    }
  }
  return cached;
}

export async function saveToken(token: string | null): Promise<void> {
  if (USES_COOKIE) return;
  cached = token;
  if (token) {
    await SecureStore.setItemAsync(KEY, token, OPTIONS);
  } else {
    await SecureStore.deleteItemAsync(KEY, OPTIONS).catch(() => {});
  }
}
