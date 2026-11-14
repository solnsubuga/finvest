import type { ClientSignedIn } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";
import { saveToken } from "@/api/token";

import { AuthClient, AuthError, AuthErrorCode, Device, SecondStep, Session } from "./types";
import { createPasskey, PasskeyCancelled, passkeysSupported, signWithPasskey } from "./webauthn";

/**
 * The sign-in screens' contract, answered by the real API
 * (backend/app/modules/identity). On the web the session is the API's
 * HttpOnly cookie; on a phone it is a token in the secure store.
 */

const PASSTHROUGH: ReadonlySet<AuthErrorCode> = new Set([
  "invalid_credentials",
  "invalid_code",
  "phone_taken",
  "leaked_password",
  "expired",
  "sign_in_again",
  "passkey_failed",
  "rate_limited",
  "not_found",
  "offline",
]);

/** Turns what the API or the network said into a reason a sign-in screen can show. */
export function toAuthError(error: unknown): AuthError {
  if (error instanceof AuthError) return error;
  if (error instanceof PasskeyCancelled) return new AuthError("cancelled");
  if (error instanceof ApiError) {
    if (PASSTHROUGH.has(error.code as AuthErrorCode)) return new AuthError(error.code as AuthErrorCode);
    // A setting-up screen whose session has gone: logging in again brings the client back.
    if (error.code === "unauthorized") return new AuthError("sign_in_again");
    if (error.code === "validation_failed") return new AuthError("not_accepted");
  }
  return new AuthError("unavailable");
}

async function wrap<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toAuthError(error);
  }
}

const SECOND_STEPS: readonly SecondStep[] = ["passkey", "authenticator", "code"];

/**
 * The API's list in its order (strongest first), less what this device cannot
 * do. The app adds nothing, so a rule change on the server is never undone here.
 */
function usable(methods: readonly string[]): SecondStep[] {
  return methods.filter(
    (method): method is SecondStep =>
      (SECOND_STEPS as readonly string[]).includes(method) && (method !== "passkey" || passkeysSupported()),
  );
}

type SessionView = {
  phone: string;
  restricted_until: string | null;
  email?: string | null;
  email_verified?: boolean;
};

export function toSession(view: SessionView, now: number = Date.now()): Session {
  const until = view.restricted_until ? Date.parse(view.restricted_until) : NaN;
  return {
    phone: view.phone,
    // The name is known only after the identity check, which the session does not carry yet.
    displayName: null,
    restricted: Number.isFinite(until) && until > now,
    email: view.email ?? null,
    emailVerified: Boolean(view.email && view.email_verified),
  };
}

async function signedIn(result: ClientSignedIn): Promise<Session> {
  // A phone gets a token to keep. A browser gets null here and a cookie instead.
  if (result.token) await saveToken(result.token);
  return toSession(result.session);
}

export function createApiAuthClient(): AuthClient {
  return {
    restoreSession: async () => {
      try {
        const view = await api("GET /client/session", {});
        return toSession(view);
      } catch (error) {
        if (error instanceof ApiError && error.code === "unauthorized") {
          await saveToken(null);
          return null;
        }
        throw toAuthError(error);
      }
    },

    startSignup: (input) =>
      wrap(async () => {
        const result = await api("POST /client/signup/start", {
          body: { phone: input.phone, email: input.email.trim() || null },
        });
        return { challengeId: result.challenge_id, phone: result.phone };
      }),

    confirmSignupCode: (challengeId, code) =>
      wrap(async () => {
        const result = await api("POST /client/signup/confirm", { body: { challenge_id: challengeId, code } });
        return { ticket: result.ticket, phone: result.phone };
      }),

    completeSignup: (ticket, password) =>
      wrap(async () => signedIn(await api("POST /client/signup/complete", { body: { ticket, password } }))),

    startLogin: (phone, password) =>
      wrap(async () => {
        const result = await api("POST /client/login/start", { body: { phone, password } });
        return { loginId: result.login_id, phone, methods: usable(result.methods) };
      }),

    verifyWithPasskey: (loginId) =>
      wrap(async () => {
        const options = await api("POST /client/login/passkey/options", { body: { login_id: loginId } });
        const credential = await signWithPasskey(options.public_key);
        return signedIn(await api("POST /client/login/passkey/verify", { body: { login_id: loginId, credential } }));
      }),

    verifyWithAuthenticator: (loginId, code) =>
      wrap(async () =>
        signedIn(await api("POST /client/login/authenticator/verify", { body: { login_id: loginId, code } })),
      ),

    sendLoginCode: (loginId) =>
      wrap(async () => {
        await api("POST /client/login/code/send", { body: { login_id: loginId, channel: "sms" } });
      }),

    verifyWithCode: (loginId, code) =>
      wrap(async () => signedIn(await api("POST /client/login/code/verify", { body: { login_id: loginId, code } }))),

    listSecondSteps: () =>
      wrap(async () => {
        const result = await api("GET /client/second-steps", {});
        return usable(result.methods);
      }),

    addPasskey: () =>
      wrap(async () => {
        if (!passkeysSupported()) throw new AuthError("passkey_unsupported");
        const options = await api("POST /client/passkeys/options", {});
        const credential = await createPasskey(options.public_key);
        await api("POST /client/passkeys", { body: { credential } });
      }),

    startAuthenticatorSetup: () =>
      wrap(async () => {
        const result = await api("POST /client/authenticator/options", {});
        return { key: result.secret, uri: result.uri };
      }),

    confirmAuthenticatorSetup: (code) =>
      wrap(async () => {
        await api("POST /client/authenticator", { body: { code } });
      }),

    startPasswordReset: (phone) =>
      wrap(async () => {
        const result = await api("POST /client/password/reset/start", { body: { phone } });
        return { challengeId: result.challenge_id, phone };
      }),

    completePasswordReset: (challengeId, code, password) =>
      wrap(async () => {
        await api("POST /client/password/reset/complete", {
          body: { challenge_id: challengeId, code, password },
        });
        // The server ended every session, this device's too.
        await saveToken(null);
      }),

    listDevices: () =>
      wrap(async () => {
        const result = await api("GET /client/sessions", {});
        return result.items.map(
          (item): Device => ({
            id: item.id,
            label: item.device,
            kind: item.transport === "token" ? "app" : "browser",
            confirmedWith: item.second_step,
            signedInAt: item.signed_in_at,
            lastSeenAt: item.last_seen_at,
            current: item.current,
          }),
        );
      }),

    signOutDevice: (id) =>
      wrap(async () => {
        await api("DELETE /client/sessions/{session_id}", { params: { session_id: id } });
      }),

    signOutOtherDevices: () =>
      wrap(async () => (await api("POST /client/sessions/sign-out-others", {})).ended),

    listPasskeys: () =>
      wrap(async () =>
        (await api("GET /client/passkeys", {})).map((item) => ({
          id: item.id,
          name: item.name,
          createdAt: item.created_at,
          lastUsedAt: item.last_used_at,
        })),
      ),

    removePasskey: (id) =>
      wrap(async () => {
        await api("DELETE /client/passkeys/{passkey_id}", { params: { passkey_id: id } });
      }),

    turnOffAuthenticator: () =>
      wrap(async () => {
        await api("DELETE /client/authenticator", {});
      }),

    sendEmailProof: () =>
      wrap(async () => ((await api("POST /client/email/verification", {})).sent ? "sent" : "proved")),

    confirmEmail: (token) =>
      wrap(async () => {
        await api("POST /client/email/verification/confirm", { body: { token } });
      }),

    signOut: async () => {
      try {
        await api("POST /client/sign-out", {});
      } catch {
        // Signing out on this device goes ahead even when the API cannot be reached.
      } finally {
        await saveToken(null);
      }
    },
  };
}
