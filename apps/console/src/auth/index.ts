import { createHttpConfirmationApi, type ConfirmationApi } from "@/api/confirmation.ts";
import { apiBaseUrl, createHttp } from "@/api/http.ts";
import { browserPasskeys } from "@/api/webauthn.ts";
import { notConnectedAuthApi, type AuthApi } from "./api.ts";
import { createHttpAuthApi } from "./http.ts";
import { createStubAuthApi } from "./stub.ts";

/**
 * True when sign-in is the sample one. It is used under `pnpm dev` unless
 * VITE_API_URL points at a running API, or when a build sets
 * VITE_STUB_API=true (for a preview). The build refuses that setting for a
 * production deploy: see vite.config.ts.
 */
export function isStubAuth(): boolean {
  if (import.meta.env.VITE_STUB_API === "true") return true;
  return Boolean(import.meta.env.DEV) && !import.meta.env.VITE_API_URL;
}

/**
 * The sample sign-in, the real API at VITE_API_URL, or, in a build with
 * neither, no sign-in at all.
 */
export function createAuthApi(): AuthApi {
  if (isStubAuth()) return createStubAuthApi();
  const base = apiBaseUrl();
  return base ? createHttpAuthApi(createHttp(base)) : notConnectedAuthApi;
}

/** The "confirm it is you" routes, or null in the sample sign-in and in a build with no API. */
export function createConfirmationApi(): ConfirmationApi | null {
  if (isStubAuth()) return null;
  const base = apiBaseUrl();
  // Its own requests never ask to confirm, so the dialog can never wait on itself.
  return base ? createHttpConfirmationApi(createHttp(base, fetch, () => Promise.resolve(false)), browserPasskeys) : null;
}
