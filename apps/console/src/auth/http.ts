import { ApiError, type Http } from "@/api/http.ts";
import { browserPasskeys, type PasskeyBrowser } from "@/api/webauthn.ts";
import {
  AuthError,
  SECOND_STEP_METHODS,
  type AuthApi,
  type AuthErrorCode,
  type SecondStepMethod,
} from "./api.ts";
import { PERMISSIONS, ROLES, type Permission, type Role, type StaffSession } from "./types.ts";

/**
 * Staff sign-in against the real API (backend/app/modules/identity). The
 * session is the HttpOnly cookie the API sets on the last step; nothing here
 * can read it.
 */

interface SessionBody {
  staff_id: string;
  name: string;
  role: string;
  permissions: string[];
}

export function toSession(body: SessionBody): StaffSession {
  if (!(ROLES as readonly string[]).includes(body.role)) throw new AuthError("unavailable");
  const known = new Set<string>(PERMISSIONS);
  return {
    staffId: body.staff_id,
    name: body.name,
    role: body.role as Role,
    // The API may know permissions this build of the console does not show yet.
    permissions: body.permissions.filter((name): name is Permission => known.has(name)),
  };
}

function methods(list: string[]): SecondStepMethod[] {
  return list.filter((method): method is SecondStepMethod =>
    (SECOND_STEP_METHODS as readonly string[]).includes(method),
  );
}

/** The API's codes, as the sign-in screens word them. */
export function toAuthError(error: unknown): AuthError {
  if (error instanceof AuthError) return error;
  if (!(error instanceof ApiError)) return new AuthError("passkey_failed");
  const byCode: Record<string, AuthErrorCode> = {
    invalid_credentials: "invalid_credentials",
    passkey_failed: "passkey_failed",
    invalid_code: "invalid_code",
    second_step_missing: "second_step_missing",
    expired: "link_expired",
    not_found: "link_expired",
    rate_limited: "rate_limited",
    leaked_password: "password_leaked",
    unauthorized: "invalid_credentials",
  };
  if (error.code === "validation_failed") {
    const field = error.fieldCode;
    if (field === "too_short") return new AuthError("password_too_short");
    if (field === "too_long") return new AuthError("password_too_long");
    if (field === "leaked_password" || field === "too_common") return new AuthError("password_leaked");
    return new AuthError("invalid_code");
  }
  const code = byCode[error.code] ?? "unavailable";
  const mapped = new AuthError(code);
  if (code === "rate_limited") mapped.retryAfterSeconds = error.retryAfterSeconds;
  return mapped;
}

async function mapped<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toAuthError(error);
  }
}

export function createHttpAuthApi(http: Http, passkeys: PasskeyBrowser = browserPasskeys): AuthApi {
  return {
    async getSession() {
      try {
        return toSession(await http.request<SessionBody>("GET", "/staff/session"));
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw toAuthError(error);
      }
    },
    startSignIn: (email, password) =>
      mapped(async () => {
        const body = await http.request<{ login_id: string; methods: string[] }>(
          "POST",
          "/staff/login/start",
          { email, password },
        );
        return { challengeId: body.login_id, methods: methods(body.methods) };
      }),
    finishSignIn: (challenge, answer) =>
      mapped(async () => {
        if (answer.method === "authenticator") {
          return toSession(
            await http.request<SessionBody>("POST", "/staff/login/authenticator/verify", {
              login_id: challenge.challengeId,
              code: answer.code,
            }),
          );
        }
        const options = await http.request<{ public_key: Record<string, unknown> }>(
          "POST",
          "/staff/login/passkey/options",
          { login_id: challenge.challengeId },
        );
        const credential = await passkeys.get(options.public_key);
        return toSession(
          await http.request<SessionBody>("POST", "/staff/login/passkey/verify", {
            login_id: challenge.challengeId,
            credential,
          }),
        );
      }),
    async signOut() {
      await http.request<void>("POST", "/staff/sign-out");
    },
    getEnrolment: (token) =>
      mapped(async () => {
        const body = await http.request<{ name: string; email: string; role: string; methods: string[] }>(
          "POST",
          "/staff/enrolment",
          { token },
        );
        if (!(ROLES as readonly string[]).includes(body.role)) throw new AuthError("unavailable");
        return { name: body.name, email: body.email, role: body.role as Role, methods: methods(body.methods) };
      }),
    enrolWithPasskey: (token, password) =>
      mapped(async () => {
        const options = await http.request<{ public_key: Record<string, unknown> }>(
          "POST",
          "/staff/enrolment/passkey/options",
          { token },
        );
        const credential = await passkeys.create(options.public_key);
        return toSession(
          await http.request<SessionBody>("POST", "/staff/enrolment/passkey/verify", {
            token,
            password,
            credential,
          }),
        );
      }),
    startAuthenticatorEnrolment: (token) =>
      mapped(() =>
        http.request<{ secret: string; uri: string }>("POST", "/staff/enrolment/authenticator/options", {
          token,
        }),
      ),
    enrolWithAuthenticator: (token, password, code) =>
      mapped(async () =>
        toSession(
          await http.request<SessionBody>("POST", "/staff/enrolment/authenticator/verify", {
            token,
            password,
            code,
          }),
        ),
      ),
  };
}
