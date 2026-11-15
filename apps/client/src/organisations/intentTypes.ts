import { CryptoDigestAlgorithm, digestStringAsync } from "expo-crypto";

/**
 * Someone who chose "Open an account for a company or group" on the sign-up screen first opens
 * their own personal account: they are the applicant, so their own ID check must be approved
 * before the company or group application can start (Solo's rule: sign-up and KYB first).
 * That can take days, so the choice is kept on this device, tied to the mobile number they
 * signed up with, and Home leads them on to the application once they can make it.
 *
 * The number itself is never stored, only its SHA-256 digest: a browser may be shared, and the
 * entry outlives signing out.
 */
export type OrganisationIntent = { digest: string };

const DIGEST = /^[0-9a-f]{64}$/;

export function digestPhone(phone: string): Promise<string> {
  return digestStringAsync(CryptoDigestAlgorithm.SHA256, phone);
}

export function parseIntent(raw: string | null): OrganisationIntent | null {
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    const digest = value && typeof value === "object" ? (value as { digest?: unknown }).digest : undefined;
    if (typeof digest === "string" && DIGEST.test(digest)) return { digest };
  } catch {
    // A damaged entry is the same as none.
  }
  return null;
}

export async function storedIntent(phone: string): Promise<string> {
  return JSON.stringify({ digest: await digestPhone(phone) });
}

/** Only the person who made the choice sees it, not someone else who later signs in on this device. */
export async function intentFor(intent: OrganisationIntent | null, phone: string | null | undefined): Promise<boolean> {
  if (intent === null || !phone) return false;
  return intent.digest === (await digestPhone(phone));
}
