import * as LocalAuthentication from "expo-local-authentication";
import * as SecureStore from "expo-secure-store";

/**
 * Fingerprint or face unlock on the phone (docs/decisions.md, 2026-11-14). The server gives this
 * session its own unlock key once the PIN is typed. The key is kept in the Keychain or Keystore
 * where only a fingerprint or face can read it, and it is sent instead of the PIN. The PIN itself
 * is never stored on the phone.
 */
const KEY = "finvest.unlock-key";
/** Says a key is kept, so the app can offer the button without asking for a fingerprint first. */
const KEPT = "finvest.unlock-key.kept";

const LOCKED: SecureStore.SecureStoreOptions = {
  requireAuthentication: true,
  keychainAccessible: SecureStore.WHEN_PASSCODE_SET_THIS_DEVICE_ONLY,
};
const PLAIN: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

/** True when this phone has a fingerprint or face set up that can guard the key. */
export async function biometricOnThisPhone(): Promise<boolean> {
  try {
    return (await LocalAuthentication.hasHardwareAsync()) && (await LocalAuthentication.isEnrolledAsync());
  } catch {
    return false;
  }
}

/** Keeps the key behind the fingerprint or face. Fails if the client cancels the prompt. */
export async function keepUnlockKey(key: string): Promise<void> {
  await SecureStore.setItemAsync(KEY, key, { ...LOCKED, authenticationPrompt: "Turn on unlock for Finvest" });
  await SecureStore.setItemAsync(KEPT, "1", PLAIN);
}

/** Whether a key is kept on this phone. Asks for no fingerprint. */
export async function unlockKeyKept(): Promise<boolean> {
  try {
    return (await SecureStore.getItemAsync(KEPT, PLAIN)) === "1";
  } catch {
    return false;
  }
}

/**
 * Asks for the fingerprint or face and reads the key. Null when the client cancels or the phone
 * refuses. A new fingerprint added to the phone makes the key unreadable, so it is then forgotten
 * and the PIN is needed.
 */
export async function readUnlockKey(): Promise<string | null> {
  try {
    const key = await SecureStore.getItemAsync(KEY, { ...LOCKED, authenticationPrompt: "Open Finvest" });
    if (key === null) await forgetUnlockKey();
    return key;
  } catch (error) {
    if (!isCancel(error)) await forgetUnlockKey();
    return null;
  }
}

export async function forgetUnlockKey(): Promise<void> {
  await SecureStore.deleteItemAsync(KEPT, PLAIN).catch(() => {});
  await SecureStore.deleteItemAsync(KEY, LOCKED).catch(() => {});
}

function isCancel(error: unknown): boolean {
  const text = error instanceof Error ? `${error.message}` : String(error);
  return /cancel/i.test(text);
}
