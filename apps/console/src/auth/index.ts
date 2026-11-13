import { notConnectedAuthApi, type AuthApi } from "./api.ts";
import { createStubAuthApi } from "./stub.ts";

/**
 * True when sign-in is the sample one. It is used under `pnpm dev`, or when a
 * build sets VITE_STUB_API=true (for a preview). The build refuses that
 * setting for a production deploy: see vite.config.ts.
 */
export function isStubAuth(): boolean {
  return import.meta.env.DEV || import.meta.env.VITE_STUB_API === "true";
}

/**
 * A normal production build gets no sign-in at all until the generated API
 * client is wired in here.
 */
export function createAuthApi(): AuthApi {
  return isStubAuth() ? createStubAuthApi() : notConnectedAuthApi;
}
