/**
 * The review queue over the real API (docs/payouts.md, "Staff routes" and "Held payouts").
 *
 * The queue lists accounts with masked numbers. Opening one shows the whole number, the name on
 * the client's ID and a five minute link to the proof, and is written to the audit trail, so an
 * account is opened only when a person chooses it.
 */

import { ApiError, type Http } from "@/api/http.ts";
import {
  ReviewQueueError,
  type AccountKind,
  type AccountStatus,
  type DecidedPayout,
  type HeldPayout,
  type NameMatch,
  type Network,
  type PayoutAccount,
  type PayoutAccountDetail,
  type ProofKind,
  type RejectionReason,
  type ReviewQueueApi,
  type ReviewQueueErrorCode,
} from "./api.ts";

interface CheckJson {
  proof_name: string;
  name_match: NameMatch;
  statement_date: string | null;
  checked_by: string;
  checked_at: string;
}

export interface AccountJson {
  id: string;
  client_id: string;
  kind: AccountKind;
  network: Network | null;
  bank_code: string | null;
  account: string;
  holder_name: string;
  status: AccountStatus;
  proof_kind: ProofKind | null;
  check: CheckJson | null;
  rejection_reason: RejectionReason | null;
  approved_by: string | null;
  approved_at: string | null;
  payable_from: string | null;
  created_at: string;
}

export interface AccountDetailJson extends AccountJson {
  account_number: string;
  id_name: string | null;
  proof_link: { url: string; expires_at: string } | null;
}

export interface HeldJson {
  id: string;
  client_id: string;
  destination_id: string;
  kind: AccountKind;
  amount: { minor: number; currency: "UGX" | "USD" };
  business_date: string;
  created_at: string;
}

export function toAccount(json: AccountJson): PayoutAccount {
  return {
    id: json.id,
    clientId: json.client_id,
    kind: json.kind,
    network: json.network,
    bankCode: json.bank_code,
    account: json.account,
    holderName: json.holder_name,
    status: json.status,
    proofKind: json.proof_kind,
    check: json.check
      ? {
          proofName: json.check.proof_name,
          nameMatch: json.check.name_match,
          statementDate: json.check.statement_date,
          checkedBy: json.check.checked_by,
          checkedAt: json.check.checked_at,
        }
      : null,
    rejectionReason: json.rejection_reason,
    approvedBy: json.approved_by,
    approvedAt: json.approved_at,
    payableFrom: json.payable_from,
    createdAt: json.created_at,
  };
}

export function toAccountDetail(json: AccountDetailJson): PayoutAccountDetail {
  return {
    ...toAccount(json),
    accountNumber: json.account_number,
    idName: json.id_name,
    proofLink: json.proof_link ? { url: json.proof_link.url, expiresAt: json.proof_link.expires_at } : null,
  };
}

export function toHeld(json: HeldJson): HeldPayout {
  return {
    id: json.id,
    clientId: json.client_id,
    destinationId: json.destination_id,
    kind: json.kind,
    amount: { minor: json.amount.minor, currency: json.amount.currency },
    businessDate: json.business_date,
    createdAt: json.created_at,
  };
}

/** Refusals the backend names in the error's field problem, which the screen words itself. */
const NAMED: readonly ReviewQueueErrorCode[] = [
  "name_different",
  "statement_date_required",
  "statement_too_old",
  "proof_kind_not_accepted",
  "client_not_verified",
  "destination_not_checked",
  "account_in_use",
  "checked_by_you",
  "payout_not_held",
];

function named(error: ApiError, otherwise: ReviewQueueErrorCode): ReviewQueueError {
  const code = error.fieldCode;
  return new ReviewQueueError(code && (NAMED as readonly string[]).includes(code) ? (code as ReviewQueueErrorCode) : otherwise);
}

export function toReviewQueueError(error: unknown): ReviewQueueError {
  if (error instanceof ReviewQueueError) return error;
  if (!(error instanceof ApiError)) return new ReviewQueueError("unavailable");
  switch (error.code) {
    // A name that is not the client's, a missing or old statement date, or a reason too long.
    case "validation_failed":
      return named(error, "invalid");
    // The account or payout moved on: approved for another client, not checked, no longer held.
    case "conflict":
      return named(error, "conflict");
    // Approving your own check is the two-person rule, not a missing permission.
    case "forbidden":
      return named(error, "forbidden");
    case "not_found":
      return new ReviewQueueError("not_found");
    case "idempotency_key_reused":
      return new ReviewQueueError("key_reused");
    // The person closed the "confirm it is you" dialog, so nothing was done.
    case "confirm_it_is_you":
      return new ReviewQueueError("not_confirmed");
    default:
      return new ReviewQueueError("unavailable");
  }
}

export function createHttpReviewQueueApi(http: Http): ReviewQueueApi {
  const account = (id: string, action = "") => `/staff/payout-destinations/${encodeURIComponent(id)}${action}`;

  async function call<T>(run: () => Promise<T>): Promise<T> {
    try {
      return await run();
    } catch (error) {
      throw toReviewQueueError(error);
    }
  }

  const act = (id: string, action: string, body: Record<string, unknown>) =>
    call(async () => toAccount(await http.request<AccountJson>("POST", account(id, action), body)));

  return {
    listAccounts: () => call(async () => (await http.request<AccountJson[]>("GET", "/staff/payout-destinations")).map(toAccount)),
    openAccount: (id) => call(async () => toAccountDetail(await http.request<AccountDetailJson>("GET", account(id)))),
    check: (id, check) =>
      act(id, "/check", {
        proof_name: check.proofName,
        ...(check.statementDate ? { statement_date: check.statementDate } : {}),
        reason: check.reason,
      }),
    approve: (id, reason) => act(id, "/approve", { reason }),
    reject: (id, rejectionReason, reason) => act(id, "/reject", { rejection_reason: rejectionReason, reason }),
    remove: (id, reason) => act(id, "/remove", { reason }),
    listHeld: () => call(async () => (await http.request<HeldJson[]>("GET", "/staff/payouts/held")).map(toHeld)),
    decideHeld: (id, decision, reason, idempotencyKey) =>
      call(() =>
        http.request<DecidedPayout>("POST", `/staff/payouts/${encodeURIComponent(id)}/${decision}`, { reason }, { idempotencyKey }),
      ),
  };
}
