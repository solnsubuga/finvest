import { notConnectedAuthApi, type AuthApi } from "./api.ts";
import { createStubAuthApi } from "./stub.ts";

/**
 * The stub is used under `pnpm dev`, or when a build sets VITE_STUB_API=true
 * (for a preview). A normal production build gets no sign-in at all until the
 * generated API client is wired in here.
 */
export function createAuthApi(): AuthApi {
  const useStub = import.meta.env.DEV || import.meta.env.VITE_STUB_API === "true";
  return useStub ? createStubAuthApi() : notConnectedAuthApi;
}
