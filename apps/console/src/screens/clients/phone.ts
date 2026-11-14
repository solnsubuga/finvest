/**
 * Changing a client's mobile number: one member of staff proposes the new number with a
 * reason, and a different one approves it (`/staff/phone-changes` and
 * `/staff/clients/{id}/phone-changes` in backend/app/modules/identity/phone_changes_api.py).
 *
 * The number is how a client proves who they are with a code, so one person alone can never
 * point it at a phone they hold. The backend and the database enforce the two people. When
 * the change is approved the client is signed out everywhere, codes already sent stop working,
 * payouts go only to known accounts for 24 hours, and the old number and the email are told.
 * A proposal nobody decides lapses after 24 hours. All of it needs `client_phones.change`.
 */

import { ApiError, type Http } from "@/api/http.ts";
import type { Directory } from "@/directory/api.ts";

export interface PhoneChange {
  id: string;
  clientId: string;
  newPhone: string;
  reason: string;
  proposedBy: string;
  /** Null when the directory cannot name them. */
  proposedByName: string | null;
  proposedAt: string;
  expiresAt: string;
  /** The signed-in person proposed it, so someone else decides. */
  yours: boolean;
}

export interface PhoneChangesApi {
  /** The change waiting for this client, if there is one. */
  waitingFor(clientId: string): Promise<PhoneChange | null>;
  propose(clientId: string, phone: string, reason: string): Promise<void>;
  approve(changeId: string): Promise<void>;
  reject(changeId: string, note: string): Promise<void>;
}

export type PhoneChangeErrorCode =
  | "invalid_phone"
  | "invalid"
  | "phone_taken"
  | "waiting"
  | "expired"
  | "not_found"
  | "no_client"
  | "forbidden"
  | "not_confirmed"
  | "not_connected"
  | "unavailable";

export class PhoneChangeError extends Error {
  readonly code: PhoneChangeErrorCode;

  constructor(code: PhoneChangeErrorCode) {
    super(code);
    this.name = "PhoneChangeError";
    this.code = code;
  }
}

const MESSAGES: Record<PhoneChangeErrorCode, string> = {
  invalid_phone: "That is not a Ugandan mobile number. Write it as 0772 123 456 or +256 772 123 456.",
  invalid: "Write a short reason for the audit trail.",
  phone_taken: "That number already belongs to another Finvest account. Nothing changed.",
  waiting: "A change of number is already waiting for this client. Approve or reject it first.",
  expired: "That proposal lapsed after 24 hours. Propose the number again if it is still needed.",
  not_found: "That proposal has already been decided. The page now shows where it stands.",
  no_client: "Finvest could not find this client. Reload the page; the account may have been closed.",
  forbidden: "You proposed this change, so someone else must decide it.",
  not_confirmed: "Nothing was changed, because you did not confirm it is you.",
  not_connected: "Number changes are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function phoneChangeErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof PhoneChangeError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new PhoneChangeError("not_connected"));

export const notConnectedPhoneChangesApi: PhoneChangesApi = {
  waitingFor: notConnected,
  propose: notConnected,
  approve: notConnected,
  reject: notConnected,
};

// Over HTTP ---------------------------------------------------------------------------------------

interface ChangeJson {
  id: string;
  client_id: string;
  new_phone: string;
  reason: string;
  proposed_by: string;
  proposed_at: string;
  expires_at: string;
}

export function toPhoneChangeError(error: unknown): PhoneChangeError {
  if (error instanceof PhoneChangeError) return error;
  if (!(error instanceof ApiError)) return new PhoneChangeError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new PhoneChangeError(error.fields.some((field) => field.field_path.includes("phone")) ? "invalid_phone" : "invalid");
    case "phone_taken":
      return new PhoneChangeError("phone_taken");
    // Another change for this client is already waiting.
    case "conflict":
      return new PhoneChangeError("waiting");
    case "expired":
      return new PhoneChangeError("expired");
    case "not_found":
      return new PhoneChangeError("not_found");
    case "forbidden":
      return new PhoneChangeError("forbidden");
    case "confirm_it_is_you":
      return new PhoneChangeError("not_confirmed");
    default:
      return new PhoneChangeError("unavailable");
  }
}

export function createHttpPhoneChangesApi(http: Http, directory: Directory, me: string): PhoneChangesApi {
  async function call<T>(run: () => Promise<T>): Promise<T> {
    try {
      return await run();
    } catch (error) {
      throw toPhoneChangeError(error);
    }
  }

  return {
    waitingFor: (clientId) =>
      call(async () => {
        const { items } = await http.request<{ items: ChangeJson[] }>("GET", "/staff/phone-changes");
        const json = items.find((item) => item.client_id === clientId);
        if (!json) return null;
        const name = await directory.staffName(json.proposed_by).catch(() => null);
        return {
          id: json.id,
          clientId: json.client_id,
          newPhone: json.new_phone,
          reason: json.reason,
          proposedBy: json.proposed_by,
          proposedByName: name,
          proposedAt: json.proposed_at,
          expiresAt: json.expires_at,
          yours: json.proposed_by === me,
        };
      }),
    propose: (clientId, phone, reason) =>
      call(async () => {
        await http.request("POST", `/staff/clients/${encodeURIComponent(clientId)}/phone-changes`, { phone, reason });
      }).catch((error: unknown) => {
        // On a proposal, not found means the client, not a proposal someone else decided.
        throw error instanceof PhoneChangeError && error.code === "not_found" ? new PhoneChangeError("no_client") : error;
      }),
    approve: (changeId) =>
      call(async () => {
        await http.request("POST", `/staff/phone-changes/${encodeURIComponent(changeId)}/approval`);
      }),
    reject: (changeId, note) =>
      call(async () => {
        await http.request("POST", `/staff/phone-changes/${encodeURIComponent(changeId)}/rejection`, { note });
      }),
  };
}

// Samples -----------------------------------------------------------------------------------------

/** A Ugandan mobile number as the backend accepts it, or null. */
export function normalisePhone(text: string): string | null {
  const digits = text.replace(/[\s()-]/g, "");
  const match = /^(?:\+?256|0)(7\d{8})$/.exec(digits);
  return match ? `+256${match[1]}` : null;
}

/**
 * Sample number changes for `pnpm dev` and previews. Grace Auma has proposed a new number for
 * the first sample client, so a reviewer can approve it; a change the signed-in person proposes
 * can only be withdrawn by its lapsing, as in the real system.
 */
export function createStubPhoneChangesApi(me: { id: string }, now: () => number = Date.now, delayMs = 120): PhoneChangesApi {
  const hour = 3_600_000;
  const changes = new Map<string, PhoneChange>();
  const taken = new Set(["+256772000111"]);
  let next = 1;
  changes.set("-NstubPhone000000001", {
    id: "-NstubPhone000000001",
    clientId: "-NstubClient00000042",
    newPhone: "+256701234567",
    reason: "Client called from the new number and answered the security questions. Old SIM was stolen.",
    proposedBy: "sample-staff-grace",
    proposedByName: "Grace Auma",
    proposedAt: new Date(now() - 2 * hour).toISOString(),
    expiresAt: new Date(now() + 22 * hour).toISOString(),
    yours: false,
  });
  const later = <T,>(value: () => T) =>
    new Promise<T>((resolve, reject) =>
      setTimeout(() => {
        try {
          resolve(value());
        } catch (error) {
          reject(error instanceof Error ? error : new Error(String(error)));
        }
      }, delayMs),
    );
  const waiting = (id: string) => {
    const change = changes.get(id);
    if (!change) throw new PhoneChangeError("not_found");
    if (change.yours) throw new PhoneChangeError("forbidden");
    return change;
  };
  return {
    waitingFor: (clientId) => later(() => [...changes.values()].find((change) => change.clientId === clientId) ?? null),
    propose: (clientId, phone, reason) =>
      later(() => {
        const normal = normalisePhone(phone);
        if (!normal) throw new PhoneChangeError("invalid_phone");
        if (!reason.trim()) throw new PhoneChangeError("invalid");
        if (taken.has(normal)) throw new PhoneChangeError("phone_taken");
        if ([...changes.values()].some((change) => change.clientId === clientId)) throw new PhoneChangeError("waiting");
        const id = `-NstubPhone${String(++next).padStart(9, "0")}`;
        changes.set(id, {
          id,
          clientId,
          newPhone: normal,
          reason: reason.trim(),
          proposedBy: me.id,
          proposedByName: "You",
          proposedAt: new Date(now()).toISOString(),
          expiresAt: new Date(now() + 24 * hour).toISOString(),
          yours: true,
        });
      }),
    approve: (changeId) =>
      later(() => {
        const change = waiting(changeId);
        changes.delete(changeId);
        taken.add(change.newPhone);
      }),
    reject: (changeId, note) =>
      later(() => {
        if (!note.trim()) throw new PhoneChangeError("invalid");
        waiting(changeId);
        changes.delete(changeId);
      }),
  };
}
