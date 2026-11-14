/**
 * Bank payouts over the real API: `/staff/payouts/bank/*`, `/staff/payout-bank-batches` and
 * `/staff/payout-bank-settlements` (docs/payouts.md).
 */

import { ApiError, type Http } from "@/api/http.ts";
import {
  BankPayoutsError,
  type Amount,
  type BankPayout,
  type BankPayoutsApi,
  type BankPayoutsErrorCode,
  type Batch,
  type BatchStatus,
  type Currency,
  type Outcome,
  type SentBankPayout,
  type Settlement,
} from "./api.ts";

interface PayoutJson {
  id: string;
  client_id: string;
  destination_id: string;
  amount: Amount;
  status: BankPayout["status"];
  business_date: string;
  created_at: string;
}

interface BatchJson {
  id: string;
  currency: Currency;
  reason: string;
  status: BatchStatus;
  prepared_by: string;
  prepared_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  payout_ids: string[];
  total: Amount;
  items?: BatchItemJson[] | null;
}

interface BatchItemJson {
  payout_id: string;
  client_id: string;
  amount: Amount;
  bank_name: string | null;
  masked_account: string;
  holder_name: string;
}

interface SettlementJson {
  id: string;
  payout_id: string;
  batch_id: string;
  outcome: Outcome;
  statement_line_id: string | null;
  statement_id: string | null;
  reason: string;
  status: "proposed" | "approved" | "declined" | "withdrawn";
  proposed_by: string;
  proposed_at: string;
}

interface SentJson {
  payout: PayoutJson;
  batch_id: string;
  sent_at: string;
  settlement: SettlementJson | null;
}

export function toPayout(json: PayoutJson): BankPayout {
  return {
    id: json.id,
    clientId: json.client_id,
    destinationId: json.destination_id,
    amount: json.amount,
    status: json.status,
    businessDate: json.business_date,
    createdAt: json.created_at,
  };
}

export function toBatch(json: BatchJson): Batch {
  return {
    id: json.id,
    currency: json.currency,
    reason: json.reason,
    status: json.status,
    preparedBy: json.prepared_by,
    preparedAt: json.prepared_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionReason: json.decision_reason,
    payoutIds: json.payout_ids,
    total: json.total,
    items:
      json.items?.map((item) => ({
        payoutId: item.payout_id,
        clientId: item.client_id,
        amount: item.amount,
        bankName: item.bank_name,
        maskedAccount: item.masked_account,
        holderName: item.holder_name,
      })) ?? null,
  };
}

function toSettlement(json: SettlementJson | null): Settlement | null {
  if (json?.status !== "proposed") return null;
  return {
    id: json.id,
    payoutId: json.payout_id,
    outcome: json.outcome,
    statementLineId: json.statement_line_id,
    statementId: json.statement_id,
    reason: json.reason,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
  };
}

export function toSent(json: SentJson): SentBankPayout {
  return { payout: toPayout(json.payout), batchId: json.batch_id, sentAt: json.sent_at, settlement: toSettlement(json.settlement) };
}

/** The refusals the backend names in `fields`, which the screen words for staff. */
const NAMED: ReadonlySet<string> = new Set<BankPayoutsErrorCode>([
  "batch_payouts_invalid",
  "currency_not_supported",
  "payout_not_bank",
  "payout_not_queued",
  "payout_in_another_batch",
  "batch_already_decided",
  "batch_not_approved",
  "prepared_by_you",
  "not_prepared_by_you",
  "payout_not_sent",
  "statement_line_required",
  "statement_required",
  "statement_line_not_this_payout",
  "statement_not_enough",
  "statement_line_in_use",
  "settlement_already_proposed",
  "settlement_already_decided",
  "proposed_by_you",
  "not_proposed_by_you",
]);

export function toBankPayoutsError(error: unknown): BankPayoutsError {
  if (error instanceof BankPayoutsError) return error;
  if (!(error instanceof ApiError)) return new BankPayoutsError("unavailable");
  const named = error.fieldCode;
  if (named && NAMED.has(named)) return new BankPayoutsError(named as BankPayoutsErrorCode);
  switch (error.code) {
    case "validation_failed":
      return new BankPayoutsError("invalid");
    case "not_found":
      return new BankPayoutsError("not_found");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case "confirm_it_is_you":
      return new BankPayoutsError("not_confirmed");
    case "forbidden":
      return new BankPayoutsError("forbidden");
    // A definite refusal this screen does not name yet, not a lost answer.
    case "conflict":
      return new BankPayoutsError("refused");
    default:
      return new BankPayoutsError("unavailable");
  }
}

export function createHttpBankPayoutsApi(http: Http): BankPayoutsApi {
  const batch = (id: string, action = "") => `/staff/payout-bank-batches/${encodeURIComponent(id)}${action ? `/${action}` : ""}`;
  const settlement = (id: string, action: string) => `/staff/payout-bank-settlements/${encodeURIComponent(id)}/${action}`;

  async function call<T>(run: () => Promise<T>): Promise<T> {
    try {
      return await run();
    } catch (error) {
      throw toBankPayoutsError(error);
    }
  }

  return {
    listWaiting: (currency) =>
      call(async () =>
        (await http.request<PayoutJson[]>("GET", `/staff/payouts/bank/waiting?currency=${currency}`)).map(toPayout),
      ),
    listBatches: () => call(async () => (await http.request<BatchJson[]>("GET", "/staff/payout-bank-batches")).map(toBatch)),
    prepare: (key, currency, payoutIds, reason) =>
      call(async () => {
        await http.request("POST", "/staff/payout-bank-batches", { currency, payout_ids: payoutIds, reason }, { idempotencyKey: key });
      }),
    approve: (key, id, reason) =>
      call(async () => {
        await http.request("POST", batch(id, "approve"), { reason }, { idempotencyKey: key });
      }),
    decline: (key, id, reason) =>
      call(async () => {
        await http.request("POST", batch(id, "decline"), { reason }, { idempotencyKey: key });
      }),
    withdraw: (id, reason) =>
      call(async () => {
        await http.request("POST", batch(id, "withdraw"), { reason });
      }),
    downloadFile: (id) => call(() => http.download(batch(id, "file"))),
    listSent: () => call(async () => (await http.request<SentJson[]>("GET", "/staff/payouts/bank/sent")).map(toSent)),
    propose: (key, payoutId, proposal) =>
      call(async () => {
        await http.request(
          "POST",
          `/staff/payouts/${encodeURIComponent(payoutId)}/bank-settlements`,
          {
            outcome: proposal.outcome,
            statement_line_id: proposal.outcome === "paid" ? proposal.statementLineId || null : null,
            statement_id: proposal.outcome === "failed" ? proposal.statementId || null : null,
            reason: proposal.reason,
          },
          { idempotencyKey: key },
        );
      }),
    approveSettlement: (key, id, reason) =>
      call(async () => {
        await http.request("POST", settlement(id, "approve"), { reason }, { idempotencyKey: key });
      }),
    declineSettlement: (id, reason) =>
      call(async () => {
        await http.request("POST", settlement(id, "decline"), { reason });
      }),
    withdrawSettlement: (id, reason) =>
      call(async () => {
        await http.request("POST", settlement(id, "withdraw"), { reason });
      }),
  };
}
