/**
 * "Confirm it is you": the API refuses a staff decision, such as approving
 * someone else's change, with `confirm_it_is_you` when the person has not
 * used their passkey or authenticator app in the last few minutes. The
 * console then asks for it once, in one dialog, and sends the refused request
 * again. The backend side is `require_confirmed` in identity/api.py (see
 * docs/security.md).
 *
 * The dialog registers itself here, so `createHttp` can ask without knowing
 * about React. With no dialog mounted, the refusal reaches the screen as is.
 */

import type { Http } from "./http.ts";
import type { PasskeyBrowser } from "./webauthn.ts";

export const CONFIRM_IT_IS_YOU = "confirm_it_is_you";

/** Resolves true once the person has confirmed, false if they closed the dialog. */
export type Confirmer = () => Promise<boolean>;

let current: Confirmer | null = null;
let asking: Promise<boolean> | null = null;

/** Returns a function that removes this confirmer again. */
export function setConfirmer(confirmer: Confirmer): () => void {
  current = confirmer;
  return () => {
    if (current === confirmer) current = null;
  };
}

/**
 * Asks the mounted dialog. Requests refused at the same moment share one ask,
 * so the person confirms once. False when no dialog is mounted.
 */
export function askToConfirm(): Promise<boolean> {
  if (!current) return Promise.resolve(false);
  if (!asking) {
    asking = current().finally(() => {
      asking = null;
    });
  }
  return asking;
}

export interface ConfirmationApi {
  /** Asks the browser for a passkey and sends it. Resolves with when the confirmation ends. */
  withPasskey(): Promise<string>;
  /** Sends a code from the authenticator app. */
  withCode(code: string): Promise<string>;
}

export function createHttpConfirmationApi(http: Http, passkeys: PasskeyBrowser): ConfirmationApi {
  return {
    async withPasskey() {
      const options = await http.request<{ public_key: Record<string, unknown> }>(
        "POST",
        "/staff/session/confirmation/passkey/options",
      );
      const credential = await passkeys.get(options.public_key);
      const body = await http.request<{ confirmed_until: string }>("POST", "/staff/session/confirmation/passkey", {
        credential,
      });
      return body.confirmed_until;
    },
    async withCode(code) {
      const body = await http.request<{ confirmed_until: string }>(
        "POST",
        "/staff/session/confirmation/authenticator",
        { code },
      );
      return body.confirmed_until;
    },
  };
}
