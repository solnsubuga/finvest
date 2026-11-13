/**
 * Email suppressions: addresses Finvest stopped emailing because a message bounced for good
 * or the client marked one as spam.
 *
 * An address is kept only as a hash, so the list shows hashes and a lookup takes the address
 * in the body of a POST, never in a URL. Seeing them needs `clients.view`. Lifting one needs
 * `email_suppressions.lift` and two people. A complaint is never lifted.
 */

import { ApiError, type Http } from "@/api/http.ts";

export type LiftStatus = "proposed" | "approved" | "rejected" | "withdrawn";
export type LiftDecision = "approved" | "rejected" | "withdrawn";

export interface Lift {
  id: string;
  addressHash: string;
  reason: string;
  proposedBy: string;
  /** Null when the API does not name staff on lifts. */
  proposedByName: string | null;
  proposedAt: string;
  status: LiftStatus;
  decidedAt: string | null;
  decisionReason: string | null;
  yours: boolean;
}

export interface Suppression {
  addressHash: string;
  reason: "bounced" | "complained";
  createdAt: string;
  lastEventAt: string | null;
  /** False once a lift was approved after the last lasting bounce. */
  suppressed: boolean;
  liftedAt: string | null;
  /** The address sent a spam complaint at some point. It is never lifted. */
  complained: boolean;
  openLift: Lift | null;
}

export interface SuppressionList {
  suppressions: Suppression[];
  canLift: boolean;
}

export interface SuppressionsApi {
  list(): Promise<SuppressionList>;
  /** Null when Finvest never stopped emailing the address. */
  lookup(email: string): Promise<Suppression | null>;
  propose(addressHash: string, reason: string): Promise<Lift>;
  decide(liftId: string, decision: LiftDecision, reason: string): Promise<Lift>;
}

export type SuppressionsErrorCode =
  | "invalid_email"
  | "invalid"
  | "complained"
  | "not_suppressed"
  | "waiting"
  | "same_person"
  | "forbidden"
  | "not_found"
  | "not_connected"
  | "unavailable";

export class SuppressionsError extends Error {
  constructor(readonly code: SuppressionsErrorCode) {
    super(code);
    this.name = "SuppressionsError";
  }
}

export function suppressionsErrorMessage(error: unknown): string {
  if (error instanceof SuppressionsError) {
    switch (error.code) {
      case "invalid_email":
        return "Enter an email address, such as name@example.com.";
      case "invalid":
        return "Write a short reason for the audit trail.";
      case "complained":
        return "This address marked a Finvest email as spam, so Finvest never emails it again.";
      case "not_suppressed":
        return "Finvest already emails this address, so there is nothing to lift.";
      case "waiting":
        return "A lift for this address is already waiting for a second person.";
      case "same_person":
        return "You proposed this, so someone else must approve or reject it.";
      case "forbidden":
        return "Your role cannot do this.";
      case "not_found":
        return "That address or proposal is no longer there.";
      case "not_connected":
        return "This console is not connected to Finvest yet.";
      case "unavailable":
        return "Finvest could not be reached. Check your connection and try again.";
    }
  }
  return "Something went wrong. Try again.";
}

export function toSuppressionsError(error: unknown, deciding = false): SuppressionsError {
  if (error instanceof SuppressionsError) return error;
  if (error instanceof ApiError) {
    const field = error.fieldCode;
    if (field === "complained" || field === "not_suppressed") return new SuppressionsError(field);
    if (error.code === "validation_failed") {
      const path = error.fields[0]?.field_path ?? [];
      return new SuppressionsError(path[path.length - 1] === "email" ? "invalid_email" : "invalid");
    }
    if (error.code === "conflict") return new SuppressionsError("waiting");
    if (error.code === "forbidden") return new SuppressionsError(deciding ? "same_person" : "forbidden");
    if (error.code === "not_found") return new SuppressionsError("not_found");
  }
  return new SuppressionsError("unavailable");
}

const refuse = () => Promise.reject(new SuppressionsError("not_connected"));

export const notConnectedSuppressionsApi: SuppressionsApi = { list: refuse, lookup: refuse, propose: refuse, decide: refuse };

interface LiftJson {
  id: string;
  address_hash: string;
  reason: string;
  proposed_by: string;
  proposed_by_name?: string | null;
  proposed_at: string;
  status: LiftStatus;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  yours: boolean;
}

interface SuppressionJson {
  address_hash: string;
  reason: Suppression["reason"];
  created_at: string;
  last_event_at: string | null;
  suppressed: boolean;
  lifted_at: string | null;
  complained: boolean;
  open_lift: LiftJson | null;
}

function toLift(json: LiftJson): Lift {
  return {
    id: json.id,
    addressHash: json.address_hash,
    reason: json.reason,
    proposedBy: json.proposed_by,
    proposedByName: json.proposed_by_name ?? null,
    proposedAt: json.proposed_at,
    status: json.status,
    decidedAt: json.decided_at,
    decisionReason: json.decision_reason,
    yours: json.yours,
  };
}

function toSuppression(json: SuppressionJson): Suppression {
  return {
    addressHash: json.address_hash,
    reason: json.reason,
    createdAt: json.created_at,
    lastEventAt: json.last_event_at,
    suppressed: json.suppressed,
    liftedAt: json.lifted_at,
    complained: json.complained,
    openLift: json.open_lift ? toLift(json.open_lift) : null,
  };
}

export function createHttpSuppressionsApi(http: Http): SuppressionsApi {
  const base = "/staff/email-suppressions";
  return {
    async list() {
      try {
        const found = await http.request<{ suppressions: SuppressionJson[]; can_lift: boolean }>("GET", base);
        return { suppressions: found.suppressions.map(toSuppression), canLift: found.can_lift };
      } catch (error) {
        throw toSuppressionsError(error);
      }
    },
    async lookup(email) {
      try {
        const found = await http.request<{ suppression: SuppressionJson | null }>("POST", `${base}/lookup`, { email: email.trim() });
        return found.suppression ? toSuppression(found.suppression) : null;
      } catch (error) {
        throw toSuppressionsError(error);
      }
    },
    async propose(addressHash, reason) {
      try {
        return toLift(await http.request<LiftJson>("POST", `${base}/${encodeURIComponent(addressHash)}/lifts`, { reason }));
      } catch (error) {
        throw toSuppressionsError(error);
      }
    },
    async decide(liftId, decision, reason) {
      try {
        return toLift(await http.request<LiftJson>("POST", `${base}/lifts/${encodeURIComponent(liftId)}/decision`, { decision, reason }));
      } catch (error) {
        throw toSuppressionsError(error, true);
      }
    },
  };
}
