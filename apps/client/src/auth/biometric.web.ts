/**
 * A browser has no app PIN, so it has no fingerprint or face unlock either. The demo build shows
 * the PIN on the web so the screens can be seen there, and here it stands in for a phone with a
 * fingerprint set up: the key is kept in memory and the "fingerprint" says yes after a moment,
 * as long as a real prompt takes.
 */
const DEMO = process.env.EXPO_PUBLIC_DEMO_AUTH === "1";

let kept: string | null = null;
const PROMPT_MS = 1500;

export async function biometricOnThisPhone(): Promise<boolean> {
  return DEMO;
}

export async function keepUnlockKey(key: string): Promise<void> {
  if (!DEMO) throw new Error("no_biometric");
  kept = key;
}

export async function unlockKeyKept(): Promise<boolean> {
  return DEMO && kept !== null;
}

export async function readUnlockKey(): Promise<string | null> {
  if (!DEMO) return null;
  await new Promise((resolve) => setTimeout(resolve, PROMPT_MS));
  return kept;
}

export async function forgetUnlockKey(): Promise<void> {
  kept = null;
}
