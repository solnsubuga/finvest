import type { NativePasskeys } from "./nativePasskeys";

export type { NativePasskeys };

/** A browser asks its own WebAuthn (webauthn.ts), so the web build carries no native module. */
export function loadNativePasskeys(): NativePasskeys | null {
  return null;
}
