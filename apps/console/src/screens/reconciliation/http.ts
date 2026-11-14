/**
 * Bank transfers over the real API: `/staff/payments/bank-transfers` in
 * backend/app/modules/payments/routes.py (see docs/payments.md).
 *
 * The routes answer with the payment and its review. Where a transfer stands
 * and what the signed-in person may do are worked out here from those, by the
 * same rules the backend enforces: no review means it needs matching, a match
 * not yet approved needs a different person, and an approved match is being
 * posted.
 */

import { CONFIRM_IT_IS_YOU } from "@/api/confirmation.ts";
import { ApiError, type Http } from "@/api/http.ts";
import {
  BankTransferError,
  type Amount,
  type BankTransfersApi,
  type QueueItem,
  type RefuseReason,
  type Review,
  type Stage,
  type StatementLine,
  type TransferAction,
  type TransferDetail,
} from "./api.ts";

type Status = "created" | "sending" | "awaiting_approval" | "uncertain" | "in_review" | "confirmed" | "failed";

interface PaymentJson {
  id: string;
  investment_id: string;
  method: "mtn" | "airtel" | "bank_transfer";
  status: Status;
  amount: Amount;
  failure: string | null;
  receipt: {
    sent_on: string;
    bank_name: string;
    transaction_number: string;
    content_type: string;
    byte_size: number;
  } | null;
  created_at: string;
  /** Not sent by the list yet. When it is, the queue shows which step each transfer waits for. */
  awaiting?: "check" | "second_person" | "posting";
}

interface LineJson {
  id: string;
  account_reference: string;
  value_date: string;
  amount: Amount;
  reference: string;
  narrative: string;
}

interface ReviewJson {
  outcome: "matched" | "not_matched";
  reason: string | null;
  statement_line_id: string | null;
  reviewed_by: string;
  reviewed_at: string;
  approved_by: string | null;
  approved_at: string | null;
}

interface TransferJson {
  payment: PaymentJson;
  client_id: string;
  receipt_link: { url: string; expires_at: string } | null;
  review: ReviewJson | null;
  matched_line: LineJson | null;
  candidates: LineJson[];
}

interface PageJson {
  items: PaymentJson[];
  next_before: string | null;
}

const BASE = "/staff/payments/bank-transfers";
/** At 50 a page, enough for any real queue. The screen never loops for ever on a bad cursor. */
const MOST_PAGES = 20;

const AWAITING: Record<NonNullable<PaymentJson["awaiting"]>, Stage> = {
  check: "to_match",
  second_person: "to_approve",
  posting: "confirming",
};

function queueStage(payment: PaymentJson): Stage {
  if (payment.status === "confirmed") return "confirmed";
  if (payment.status === "failed") return "refused";
  return payment.awaiting ? AWAITING[payment.awaiting] : "in_review";
}

function toQueueItem(payment: PaymentJson): QueueItem {
  return {
    id: payment.id,
    amount: payment.amount,
    sentOn: payment.receipt?.sent_on ?? payment.created_at.slice(0, 10),
    bankName: payment.receipt?.bank_name ?? "",
    submittedAt: payment.created_at,
    stage: queueStage(payment),
  };
}

function toLine(json: LineJson): StatementLine {
  return {
    id: json.id,
    accountReference: json.account_reference,
    valueDate: json.value_date,
    amount: json.amount,
    reference: json.reference,
    narrative: json.narrative,
  };
}

function detailStage(payment: PaymentJson, review: ReviewJson | null): Stage {
  if (payment.status === "confirmed") return "confirmed";
  if (payment.status === "failed" || review?.outcome === "not_matched") return "refused";
  if (review === null) return "to_match";
  return review.approved_by === null ? "to_approve" : "confirming";
}

function allowedFor(stage: Stage, review: ReviewJson | null, me: string): TransferAction[] {
  if (stage === "to_match") return ["match", "refuse"];
  // The person who matched it cannot approve it. The backend refuses it too.
  if (stage === "to_approve" && review && review.reviewed_by !== me) return ["approve"];
  return [];
}

export function toTransferDetail(json: TransferJson, me: string): TransferDetail {
  const { payment, review } = json;
  const stage = detailStage(payment, review);
  const reviewView: Review | null = review && {
    outcome: review.outcome,
    reason: (review.reason as RefuseReason | null) ?? null,
    reviewedByYou: review.reviewed_by === me,
    reviewedAt: review.reviewed_at,
    approvedByYou: review.approved_by === null ? null : review.approved_by === me,
    approvedAt: review.approved_at,
  };
  return {
    ...toQueueItem(payment),
    stage,
    investmentId: payment.investment_id,
    transactionNumber: payment.receipt?.transaction_number ?? "",
    receipt: {
      contentType: payment.receipt?.content_type ?? "",
      byteSize: payment.receipt?.byte_size ?? 0,
      url: json.receipt_link?.url ?? null,
    },
    review: reviewView,
    matchedLine: json.matched_line ? toLine(json.matched_line) : null,
    candidates: json.candidates.map(toLine),
    allowed: allowedFor(stage, review, me),
  };
}

/** `approving` is true for an approval, where a bare refusal means the person matched it themselves. */
export function toBankTransferError(error: unknown, approving = false): BankTransferError {
  if (error instanceof BankTransferError) return error;
  if (!(error instanceof ApiError)) return new BankTransferError("unavailable");
  switch (error.code) {
    case "not_found":
      return new BankTransferError("not_found");
    // A key spent on a different request means this try was refused unread. Reloading shows
    // what the first one did, and the next try gets a key of its own.
    case "idempotency_key_reused":
    case "conflict":
      return new BankTransferError("conflict");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case CONFIRM_IT_IS_YOU:
      return new BankTransferError("not_confirmed");
    case "forbidden":
      return new BankTransferError(approving ? "same_person" : "forbidden");
    case "validation_failed":
      // The line was taken by another payment, or stopped fitting this transfer.
      return new BankTransferError(error.fieldCode === "line_already_used" ? "line_already_used" : "line_not_found");
    default:
      return new BankTransferError("unavailable");
  }
}

export function createHttpBankTransfersApi(http: Http, me: string): BankTransfersApi {
  async function call<T>(work: () => Promise<T>, approving = false): Promise<T> {
    try {
      return await work();
    } catch (error) {
      throw toBankTransferError(error, approving);
    }
  }
  const path = (id: string) => `${BASE}/${encodeURIComponent(id)}`;
  const detail = (json: TransferJson) => toTransferDetail(json, me);

  return {
    listQueue: () =>
      call(async () => {
        const items: QueueItem[] = [];
        let before: string | null = null;
        for (let page = 0; page < MOST_PAGES; page += 1) {
          const query: string = before ? `?before=${encodeURIComponent(before)}` : "";
          const found: PageJson = await http.request<PageJson>("GET", `${BASE}${query}`);
          items.push(...found.items.map(toQueueItem));
          before = found.next_before;
          if (!before) break;
        }
        return items;
      }),
    getTransfer: (id) => call(async () => detail(await http.request<TransferJson>("GET", path(id)))),
    match: (key, id, statementLineId) =>
      call(async () =>
        detail(
          await http.request<TransferJson>(
            "POST",
            `${path(id)}/review`,
            { outcome: "matched", statement_line_id: statementLineId },
            { idempotencyKey: key },
          ),
        ),
      ),
    refuse: (key, id, reason) =>
      call(async () =>
        detail(
          await http.request<TransferJson>("POST", `${path(id)}/review`, { outcome: "not_matched", reason }, { idempotencyKey: key }),
        ),
      ),
    approve: (key, id) =>
      call(
        async () => detail(await http.request<TransferJson>("POST", `${path(id)}/approve`, undefined, { idempotencyKey: key })),
        true,
      ),
  };
}
