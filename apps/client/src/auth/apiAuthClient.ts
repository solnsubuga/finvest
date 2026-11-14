import type { ClientSignedIn } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";
import { saveToken } from "@/api/token";
import { forgetPush } from "@/push/registration";

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
  "confirm_it_is_you",
]);

/** Turns what the API or the network said into a reason a sign-in screen can show. */
export function toAuthError(error: unknown): AuthError {
  if (error instanceof AuthError) return error;
  if (error instanceof PasskeyCancelled) return new AuthError("cancelled");
  if (error instanceof ApiError) {
    if (PASSTHROUGH.has(error.code as AuthErrorCode)) return new AuthError(error.code as AuthErrorCode);
    // A setting-up screen whose session has gone: logging in again brings the client back.
    if (error.code === "unauthorized") return new AuthError("sign_in_again");
    if (error.code === "validation_failed") {
      const problem = error.fields[0]?.code;
      if (problem === "leaked_password") return new AuthError("leaked_password");
      if (problem === "same_as_now") return new AuthError("same_as_now");
      return new AuthError("not_accepted");
    }
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
  pin_set?: boolean;
  locked?: boolean;
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
    pinSet: Boolean(view.pin_set),
    locked: Boolean(view.locked),
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

    startLogin: (identifier, password) =>
      wrap(async () => {
        const byEmail = "email" in identifier;
        try {
          const result = await api("POST /client/login/start", {
            body: byEmail ? { email: identifier.email.trim(), password } : { phone: identifier.phone, password },
          });
          return {
            loginId: result.login_id,
            phone: byEmail ? null : identifier.phone,
            email: byEmail ? identifier.email.trim() : null,
            methods: usable(result.methods),
          };
        } catch (error) {
          const refused = toAuthError(error);
          throw byEmail && refused.code === "invalid_credentials" ? new AuthError("invalid_email_credentials") : refused;
        }
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

    setPin: (pin) =>
      wrap(async () => {
        try {
          await api("PUT /client/pin", { body: { pin } });
        } catch (error) {
          if (error instanceof ApiError && error.code === "validation_failed") throw new AuthError("pin_too_simple");
          throw error;
        }
      }),

    unlockWithPin: (pin) =>
      wrap(async () => {
        try {
          const result = await api("POST /client/pin/unlock", { body: { pin } });
          return { unlocked: result.unlocked, triesLeft: result.tries_left };
        } catch (error) {
          // The fifth wrong PIN ended the session on the server.
          if (error instanceof ApiError && error.code === "unauthorized") {
            await saveToken(null);
            throw new AuthError("signed_out");
          }
          throw error;
        }
      }),

    confirmWithPin: (pin, action) =>
      wrap(async () => {
        try {
          const result = await api("POST /client/pin/confirm", { body: { pin, action } });
          return { confirmed: result.confirmed, triesLeft: result.tries_left, confirmation: result.confirmation ?? null };
        } catch (error) {
          // The fifth wrong PIN ended the session on the server.
          if (error instanceof ApiError && error.code === "unauthorized") {
            await saveToken(null);
            throw new AuthError("signed_out");
          }
          throw error;
        }
      }),

    turnOnUnlockKey: (pin) =>
      wrap(async () => {
        try {
          const result = await api("PUT /client/pin/unlock-key", { body: { pin } });
          return { enabled: result.enabled, triesLeft: result.tries_left, key: result.key ?? null };
        } catch (error) {
          // The fifth wrong PIN ended the session on the server.
          if (error instanceof ApiError && error.code === "unauthorized") {
            await saveToken(null);
            throw new AuthError("signed_out");
          }
          throw error;
        }
      }),

    turnOffUnlockKey: () =>
      wrap(async () => {
        await api("DELETE /client/pin/unlock-key", {});
      }),

    unlockWithKey: (key) =>
      wrap(async () => {
        try {
          const result = await api("POST /client/pin/unlock-with-key", { body: { key } });
          return { unlocked: result.unlocked, triesLeft: result.tries_left };
        } catch (error) {
          if (error instanceof ApiError && error.code === "unauthorized") {
            await saveToken(null);
            throw new AuthError("signed_out");
          }
          throw error;
        }
      }),

    sendLoginCode: (loginId, channel) =>
      wrap(async () => {
        await api("POST /client/login/code/send", { body: { login_id: loginId, channel } });
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

    requestRecovery: ({ phone, contactPhone, reason }) =>
      wrap(async () => {
        await api("POST /client/recovery-requests", {
          body: { phone, contact_phone: contactPhone, reason },
        });
      }),

    startClaim: (token) =>
      wrap(async () => {
        const result = await api("POST /client/claim/start", { body: { token } });
        return { challengeId: result.challenge_id };
      }),

    completeClaim: (token, challengeId, code, password) =>
      wrap(async () =>
        signedIn(
          await api("POST /client/claim/complete", {
            body: { token, challenge_id: challengeId, code, password },
          }),
        ),
      ),

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

    confirmWithPasskey: () =>
      wrap(async () => {
        if (!passkeysSupported()) throw new AuthError("passkey_unsupported");
        const options = await api("POST /client/session/confirmation/passkey/options", {});
        const credential = await signWithPasskey(options.public_key);
        await api("POST /client/session/confirmation/passkey", { body: { credential } });
      }),

    confirmWithAuthenticator: (code) =>
      wrap(async () => {
        await api("POST /client/session/confirmation/authenticator", { body: { code } });
      }),

    changePassword: (current, next) =>
      wrap(async () => {
        await api("PUT /client/password", { body: { current_password: current, new_password: next } });
      }),

    startPhoneChange: (phone) =>
      wrap(async () => (await api("POST /client/phone-change", { body: { phone } })).challenge_id),

    confirmPhoneChange: (challengeId, code) =>
      wrap(async () => {
        const result = await api("POST /client/phone-change/{challenge_id}/confirmation", {
          params: { challenge_id: challengeId },
          body: { code },
        });
        return result.phone;
      }),

    changeEmail: (email) =>
      wrap(async () => (await api("PUT /client/email", { body: { email } })).email),

    signOut: async () => {
      try {
        // While the session still works, so the API knows whose phone to stop.
        await forgetPush();
        await api("POST /client/sign-out", {});
      } catch {
        // Signing out on this device goes ahead even when the API cannot be reached.
      } finally {
        await saveToken(null);
      }
    },
  };
}
