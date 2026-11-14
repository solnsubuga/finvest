/**
 * Requests from clients who cannot get in, asking Finvest to call them back. Only a number that
 * has an account reaches this list. There is no free text: the number on the account, a number
 * to call now and one of four reasons.
 *
 * Staff call the person on the number they gave, check who they are against their ID, change
 * the number through the two-person phone change if it changed, then close the request. Seeing
 * and closing them needs `client_phones.change`, the permission the phone change needs.
 */

import { ApiError, type Http } from "@/api/http.ts";

export type RecoveryReason = "lost_phone" | "new_number" | "no_codes" | "other";
export type RecoveryOutcome = "number_changed" | "helped" | "not_the_client";

export interface RecoveryRequest {
  id: string;
  clientId: string;
  accountPhone: string;
  contactPhone: string;
  reason: RecoveryReason;
  createdAt: string;
}

export interface RecoveryApi {
  /** Open requests, oldest first. */
  list(): Promise<RecoveryRequest[]>;
  close(id: string, outcome: RecoveryOutcome): Promise<void>;
}

export const REASON_LABELS: Record<RecoveryReason, string> = {
  lost_phone: "Lost their phone or SIM card",
  new_number: "Changed their number",
  no_codes: "Their codes do not arrive",
  other: "Something else",
};

export const OUTCOME_LABELS: Record<RecoveryOutcome, string> = {
  number_changed: "Number changed",
  helped: "Helped another way",
  not_the_client: "Not the client",
};

export type RecoveryErrorCode = "forbidden" | "not_found" | "not_connected" | "not_confirmed" | "refused" | "unavailable";

export class RecoveryError extends Error {
  constructor(readonly code: RecoveryErrorCode) {
    super(code);
    this.name = "RecoveryError";
  }
}

const MESSAGES: Record<RecoveryErrorCode, string> = {
  forbidden: "Your role cannot do this.",
  not_found: "Someone else has already closed that request.",
  not_connected: "This console is not connected to Finvest yet.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  refused: "Finvest refused this. Reload the page and try again, and tell the engineers if it happens again.",
  unavailable: "Finvest could not be reached. Check your connection and try again.",
};

export function recoveryErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof RecoveryError ? error.code : "unavailable"];
}

export function toRecoveryError(error: unknown): RecoveryError {
  if (error instanceof RecoveryError) return error;
  if (error instanceof ApiError) {
    if (error.code === "forbidden") return new RecoveryError("forbidden");
    if (error.code === "not_found") return new RecoveryError("not_found");
    if (error.code === "confirm_it_is_you") return new RecoveryError("not_confirmed");
    // Only a lost connection or a server failure reads as could not be reached.
    if (error.code !== "unavailable") return new RecoveryError("refused");
  }
  return new RecoveryError("unavailable");
}

const refuse = () => Promise.reject(new RecoveryError("not_connected"));

export const notConnectedRecoveryApi: RecoveryApi = { list: refuse, close: refuse };

interface RecoveryJson {
  id: string;
  client_id: string;
  account_phone: string;
  contact_phone: string;
  reason: RecoveryReason;
  created_at: string;
}

export function createHttpRecoveryApi(http: Http): RecoveryApi {
  const base = "/staff/recovery-requests";
  return {
    async list() {
      try {
        const found = await http.request<{ items: RecoveryJson[] }>("GET", base);
        return found.items.map((item) => ({
          id: item.id,
          clientId: item.client_id,
          accountPhone: item.account_phone,
          contactPhone: item.contact_phone,
          reason: item.reason,
          createdAt: item.created_at,
        }));
      } catch (error) {
        throw toRecoveryError(error);
      }
    },
    async close(id, outcome) {
      try {
        await http.request<void>("POST", `${base}/${encodeURIComponent(id)}/close`, { outcome });
      } catch (error) {
        throw toRecoveryError(error);
      }
    },
  };
}

/**
 * Sample requests for `pnpm dev` and preview builds, for two of the sample clients.
 */
export function createStubRecoveryApi(now: () => number = Date.now, delayMs = 120): RecoveryApi {
  const ago = (minutes: number) => new Date(now() - minutes * 60_000).toISOString();
  const open: RecoveryRequest[] = [
    {
      id: "-NstubRecovery000001",
      clientId: "-NstubClient00000042",
      accountPhone: "+256772123456",
      contactPhone: "+256701555013",
      reason: "lost_phone",
      createdAt: ago(95),
    },
    {
      id: "-NstubRecovery000002",
      clientId: "-NstubClient00000017",
      accountPhone: "+256701987654",
      contactPhone: "+256701987654",
      reason: "no_codes",
      createdAt: ago(20),
    },
  ];
  const wait = () => new Promise((resolve) => setTimeout(resolve, delayMs));
  return {
    async list() {
      await wait();
      return open.map((item) => ({ ...item }));
    },
    async close(id) {
      await wait();
      const index = open.findIndex((item) => item.id === id);
      if (index < 0) throw new RecoveryError("not_found");
      open.splice(index, 1);
    },
  };
}
