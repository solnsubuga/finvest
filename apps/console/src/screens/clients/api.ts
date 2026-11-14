/**
 * One client, as the Clients screen sees them: who they are, what Finvest holds for them,
 * where their payouts go, and whether they are locked out.
 *
 * Seeing a client needs `clients.view`. Locking or unlocking needs `clients.lock` and two
 * people: one proposes with a reason, a different one approves. The API checks both again.
 */

import { ApiError } from "@/api/http.ts";

export type ClientStatus = "unclaimed" | "active" | "locked" | "closed";
export type LockKind = "lock" | "unlock";
export type LockStatus = "proposed" | "approved" | "rejected" | "withdrawn";
export type Decision = "approved" | "rejected" | "withdrawn";
export type Currency = "UGX" | "USD";

export interface Account {
  id: string;
  phone: string;
  email: string | null;
  status: ClientStatus;
  createdAt: string;
  /** Sign-in is held back until then, after too many wrong tries. */
  restrictedUntil: string | null;
  hasPasskey: boolean;
  hasAuthenticator: boolean;
  lastSignedInAt: string | null;
}

export interface IdentityCheck {
  id: string;
  status: string;
  submittedAt: string | null;
  decidedAt: string | null;
  accountNumber: string | null;
}

export interface Owed {
  accountCode: string;
  accountReference: string | null;
  /** Whole units of the smallest denomination: shillings for UGX, cents for USD. */
  minor: number;
  currency: Currency;
}

export interface Destination {
  id: string;
  kind: string;
  network: string | null;
  bankCode: string | null;
  maskedAccount: string;
  holderName: string;
  status: string;
  createdAt: string;
}

export interface Lock {
  id: string;
  kind: LockKind;
  reason: string;
  proposedByName: string;
  proposedAt: string;
  status: LockStatus;
  decidedByName: string | null;
  decidedAt: string | null;
  decisionReason: string | null;
  /** The reader proposed it, so may withdraw it but never approve it. */
  yours: boolean;
}

/** The client froze their own account from the app (docs/client-profile.md). */
export interface Freeze {
  id: string;
  frozenAt: string;
  reason: string | null;
}

export interface Conversation {
  id: string;
  topic: string;
  status: string;
  lastMessageAt: string;
  waitingSince: string | null;
}

export interface Complaint {
  id: string;
  reference: string;
  category: string;
  summary: string;
  status: string;
  receivedAt: string;
  dueOn: string;
  overdue: boolean;
}

export interface ClientPage {
  account: Account;
  verifiedName: string | null;
  identityCheck: IdentityCheck | null;
  owed: Owed[];
  destinations: Destination[];
  /** Newest first. At most one is `proposed`. */
  locks: Lock[];
  /** Each time the client froze the account themselves, newest first. */
  freezes: Freeze[];
  /** Null when the reader's role does not see support chats. */
  conversation: Conversation | null;
  /** Null when the reader's role does not see complaints. */
  complaints: Complaint[] | null;
  canLock: boolean;
}

export interface ClientsApi {
  /** By client ID, ten-digit account number or mobile number. At most one client. */
  search(query: string): Promise<Account[]>;
  client(id: string): Promise<ClientPage>;
  propose(clientId: string, kind: LockKind, reason: string): Promise<Lock>;
  decide(lockId: string, decision: Decision, reason: string): Promise<Lock>;
}

export type ClientsErrorCode =
  | "invalid"
  | "already_locked"
  | "not_locked"
  | "closed"
  | "waiting"
  | "same_person"
  | "forbidden"
  | "not_found"
  | "not_connected"
  | "not_confirmed"
  | "unavailable";

export class ClientsError extends Error {
  constructor(readonly code: ClientsErrorCode) {
    super(code);
    this.name = "ClientsError";
  }
}

export function clientsErrorMessage(error: unknown): string {
  if (error instanceof ClientsError) {
    switch (error.code) {
      case "invalid":
        return "Write a short reason for the audit trail.";
      case "already_locked":
        return "This account is already locked.";
      case "not_locked":
        return "This account is not locked, so there is nothing to unlock.";
      case "closed":
        return "This account is closed, so it cannot be locked or unlocked.";
      case "waiting":
        return "Something changed while you were looking. A lock or unlock may already be waiting, or the account has moved on. The page now shows where it stands.";
      case "same_person":
        return "You proposed this, so someone else must approve or reject it.";
      case "forbidden":
        return "Your role cannot do this.";
      case "not_found":
        return "That client or proposal is no longer there.";
      case "not_connected":
        return "This console is not connected to Finvest yet.";
      case "not_confirmed":
        return "Not done. Confirm it is you with your passkey or authenticator app, then try again.";
      case "unavailable":
        return "Finvest could not be reached. Check your connection and try again.";
    }
  }
  return "Something went wrong. Try again.";
}

/** Turns what the API answered into the screen's own error. */
export function toClientsError(error: unknown, deciding = false): ClientsError {
  if (error instanceof ClientsError) return error;
  if (error instanceof ApiError) {
    const field = error.fieldCode;
    if (field === "already_locked" || field === "not_locked" || field === "closed") return new ClientsError(field);
    if (error.code === "validation_failed") return new ClientsError("invalid");
    if (error.code === "conflict") return new ClientsError("waiting");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    if (error.code === "confirm_it_is_you") return new ClientsError("not_confirmed");
    if (error.code === "forbidden") return new ClientsError(deciding ? "same_person" : "forbidden");
    if (error.code === "not_found") return new ClientsError("not_found");
  }
  return new ClientsError("unavailable");
}

const refuse = () => Promise.reject(new ClientsError("not_connected"));

export const notConnectedClientsApi: ClientsApi = {
  search: refuse,
  client: refuse,
  propose: refuse,
  decide: refuse,
};
