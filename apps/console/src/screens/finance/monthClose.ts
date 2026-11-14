/**
 * Closing the month, as the Finance screen sees it.
 *
 * Closing a month is Finance's sign-off that it is finished: every day in it is closed in the
 * ledger and its books balance. One person proposes and a different person approves. It moves
 * nothing. It keeps the month's books, in minor units, exactly as they were when proposed, and
 * approving is refused if they have changed since.
 */

import { ApiError } from "@/api/http.ts";
import type { Currency } from "./api.ts";

export type CloseStatus = "proposed" | "approved" | "declined" | "withdrawn";

export interface BookLine {
  currency: Currency;
  code: string;
  kind: string;
  opening: number;
  debits: number;
  credits: number;
  closing: number;
}

export interface MonthClose {
  id: string;
  /** `YYYY-MM` */
  month: string;
  status: CloseStatus;
  books: BookLine[];
  booksSha256: string;
  reason: string;
  proposedBy: string;
  proposedByName: string | null;
  proposedAt: string;
  decidedBy: string | null;
  decidedByName: string | null;
  decidedAt: string | null;
  decisionReason: string | null;
  /** The reader proposed it: they may withdraw it, never approve it. */
  yours: boolean;
}

export interface MonthCloses {
  /** `YYYY-MM`, or null when no month is closed yet. */
  lastClosed: string | null;
  next: {
    month: string;
    /** The last day closed in the ledger, `YYYY-MM-DD`. */
    closedThrough: string | null;
    /** Null when the month can be proposed now. */
    refusal: string | null;
  };
  /** The latest twelve, newest first. */
  closes: MonthClose[];
}

export type CloseDecision = "approve" | "decline" | "withdraw";

export interface MonthCloseApi {
  getCloses(): Promise<MonthCloses>;
  propose(month: string, reason: string, idempotencyKey: string): Promise<MonthClose>;
  /** `approve` carries the key; the others are safe to send twice. */
  decide(id: string, decision: CloseDecision, reason: string, idempotencyKey: string): Promise<MonthClose>;
}

export type MonthCloseErrorCode = "refused" | "forbidden" | "not_found" | "not_connected" | "unavailable" | "unknown";

export class MonthCloseError extends Error {
  constructor(
    readonly code: MonthCloseErrorCode,
    /** The API's reason, such as `days_not_closed` or `books_changed`. */
    readonly reason: string | null = null,
  ) {
    super(code);
    this.name = "MonthCloseError";
  }
}

export function toMonthCloseError(error: unknown): MonthCloseError {
  if (error instanceof MonthCloseError) return error;
  if (error instanceof ApiError) {
    if (error.code === "unavailable") return new MonthCloseError("unavailable");
    if (error.fieldCode) return new MonthCloseError("refused", error.fieldCode);
    if (error.code === "not_found" || error.status === 404) return new MonthCloseError("not_found");
    if (error.code === "forbidden" || error.status === 403) return new MonthCloseError("forbidden");
    return new MonthCloseError("unknown");
  }
  return new MonthCloseError("unavailable");
}

/** Why a month cannot be proposed yet, in words. `month` and `closedThrough` are already formatted. */
export function refusalText(reason: string, month: string, closedThrough: string | null): string {
  switch (reason) {
    case "month_not_ended":
      return `${month} has not ended yet.`;
    case "month_already_closed":
      return `${month} is already closed.`;
    case "close_months_in_order":
      return "Months close in order. Close the month after the last one closed first.";
    case "days_not_closed":
      return closedThrough
        ? `Every day in ${month} must be closed in the ledger first. Days are closed through ${closedThrough}.`
        : `Every day in ${month} must be closed in the ledger first. No day is closed yet.`;
    case "books_do_not_balance":
      return `The books for ${month} do not balance in one currency. Reconcile them first.`;
    case "close_already_proposed":
      return "A close is already waiting for a second person.";
    default:
      return `${month} cannot be closed yet.`;
  }
}

const REFUSED: Record<string, string> = {
  reason_required: "Give a reason. It is kept on the audit trail.",
  books_changed: "The books for that month changed after it was proposed, so it cannot be approved. Decline it and propose the month again.",
  proposed_by_you: "You proposed this close, so a different person must approve or decline it.",
  not_proposed_by_you: "Only the person who proposed a close can withdraw it.",
  not_waiting: "This close is no longer waiting. The list has been refreshed.",
};

export function monthCloseErrorMessage(error: unknown, month = "That month"): string {
  const known = toMonthCloseError(error);
  switch (known.code) {
    case "refused":
      return REFUSED[known.reason ?? ""] ?? refusalText(known.reason ?? "", month, null);
    case "forbidden":
      return "Your role cannot close months.";
    case "not_found":
      return "That close is no longer there. The list has been refreshed.";
    case "not_connected":
      return "This console is not connected to Finvest yet.";
    case "unavailable":
      return "Finvest could not be reached. Check your connection and try again.";
    case "unknown":
      return "Something went wrong. Try again.";
  }
}

const refuse = () => Promise.reject(new MonthCloseError("not_connected"));

export const notConnectedMonthCloseApi: MonthCloseApi = { getCloses: refuse, propose: refuse, decide: refuse };
