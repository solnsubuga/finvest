import type { DestinationOut, LimitsOut, MoneyView, PayoutOut } from "@finvest/api-client/client";

import { api, ApiError } from "@/api/http";
import { uploadable } from "@/payments/apiPaymentsClient";
import type { Money } from "@/payments/types";

import { banksOrNone, nameOf, readBanks } from "./banks";
import { Bank, Limits, Payout, PayoutAccount, PayoutsClient, PayoutsError } from "./types";

/**
 * The payout screens' contract, answered by the real API
 * (backend/app/modules/payouts/api.py). Every amount is passed through as the API sent it,
 * in whole numbers of the smallest unit. Account numbers arrive masked and stay that way.
 */

function money(view: MoneyView): Money {
  return { currency: view.currency, minor: view.minor };
}

export function toAccount(view: DestinationOut, banks: Bank[]): PayoutAccount {
  return {
    id: view.id,
    kind: view.kind,
    network: view.network,
    bankCode: view.bank_code,
    bankName: view.kind === "bank" ? nameOf(banks, view.bank_code) : null,
    account: view.account,
    holderName: view.holder_name,
    status: view.status,
    proofKind: view.proof_kind,
    rejectionReason: view.rejection_reason,
    nameCheck: view.name_check ?? null,
    payableFrom: view.payable_from,
    firstPaidAt: view.first_paid_at,
    createdAt: view.created_at,
  };
}

export function toPayout(view: PayoutOut): Payout {
  return {
    id: view.id,
    destinationId: view.destination_id,
    kind: view.kind,
    amount: money(view.amount),
    status: view.status,
    businessDate: view.business_date,
    createdAt: view.created_at,
    returnedAt: view.returned_at ?? null,
    resentAs: view.resent_as ?? null,
  };
}

export function toLimits(view: LimitsOut): Limits {
  return {
    effectiveFrom: view.effective_from,
    mobileMoneyPerPayout: money(view.mobile_money_per_payout),
    mobileMoneyPerDay: view.mobile_money_per_day ? money(view.mobile_money_per_day) : null,
    bankPerPayout: view.bank_per_payout ? money(view.bank_per_payout) : null,
    bankPerDay: view.bank_per_day ? money(view.bank_per_day) : null,
  };
}

const FIELD_CODES: Record<string, PayoutsError["code"]> = {
  phone_invalid: "phone_invalid",
  phone_network_unsupported: "phone_network_unsupported",
  bank_invalid: "bank_invalid",
  // A code the backend's list of banks does not hold.
  bank_unknown: "bank_invalid",
  bank_account_number_invalid: "account_number_invalid",
  // Sending a payout that came back again, to an account that cannot be paid as it stands.
  destination_not_approved: "account_not_ready",
  destination_in_safety_wait: "account_not_ready",
  destination_not_known_yet: "account_not_ready",
  over_mobile_money_limit: "over_mobile_money_limit",
  over_bank_limit: "over_limit",
  over_daily_limit: "over_limit",
};

/**
 * Turns what the API or the network said into a reason a payout screen can show. Only a
 * definite refusal gets its own reason; an unknown outcome is "unavailable", and the screen
 * reads the account again rather than assuming nothing happened.
 */
export function toPayoutsError(error: unknown): PayoutsError {
  if (error instanceof PayoutsError) return error;
  if (!(error instanceof ApiError) || error.uncertain) return new PayoutsError("unavailable");
  const codes = error.fields.map((field) => field.code);
  switch (error.code) {
    case "not_found":
      return new PayoutsError("not_found");
    case "pin_required":
      return new PayoutsError("pin_required");
    case "invalid_code":
      return new PayoutsError("invalid_code");
    case "expired":
      return new PayoutsError("code_expired");
    case "rate_limited":
      return new PayoutsError("too_many_codes");
    case "payload_too_large":
      return new PayoutsError("file_not_accepted");
    case "idempotency_key_reused":
      // The only call that can be refused this way: a payout that came back, already sent again
      // to another account.
      return new PayoutsError("already_resent");
    case "conflict":
      if (codes.includes("payout_not_returned")) return new PayoutsError("not_returned");
      if (codes.includes("too_many_unconfirmed_accounts")) return new PayoutsError("too_many_unconfirmed");
      if (codes.includes("name_check_in_progress")) return new PayoutsError("name_check_in_progress");
      if (codes.includes("name_check_limit_reached")) return new PayoutsError("name_check_limit_reached");
      if (codes.includes("client_not_verified")) return new PayoutsError("id_not_approved");
      // A conflict with no reason is the same account saved twice. Any other reason means the
      // account is no longer at the step the screen showed.
      return new PayoutsError(codes.length === 0 ? "already_saved" : "moved_on");
    case "validation_failed": {
      for (const code of codes) {
        const known = FIELD_CODES[code];
        if (known) return new PayoutsError(known);
      }
      const fields = error.fields.map((field) => field.field);
      if (fields.some((field) => field.endsWith("file"))) return new PayoutsError("file_not_accepted");
      if (fields.some((field) => field.endsWith("holder_name"))) return new PayoutsError("holder_name_invalid");
      if (fields.some((field) => field.endsWith("phone"))) return new PayoutsError("phone_invalid");
      if (fields.some((field) => field.endsWith("account_number"))) return new PayoutsError("account_number_invalid");
      if (fields.some((field) => field.endsWith("bank_code"))) return new PayoutsError("bank_invalid");
      if (fields.some((field) => field.endsWith("code"))) return new PayoutsError("invalid_code");
      return new PayoutsError("invalid");
    }
    default:
      return new PayoutsError("unavailable");
  }
}

async function wrap<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (error) {
    throw toPayoutsError(error);
  }
}

export function createApiPayoutsClient(): PayoutsClient {
  return {
    listBanks: () => wrap(readBanks),

    listAccounts: () =>
      wrap(async () => {
        const [views, banks] = await Promise.all([api("GET /client/payout-destinations", {}), banksOrNone()]);
        return views.map((view) => toAccount(view, banks));
      }),

    getAccount: (accountId) =>
      wrap(async () =>
        toAccount(
          await api("GET /client/payout-destinations/{destination_id}", { params: { destination_id: accountId } }),
          await banksOrNone(),
        ),
      ),

    addAccount: (account) => {
      // Only a phone with a PIN sends one; a browser leaves it out.
      const pin = account.pinConfirmation ? { pin_confirmation: account.pinConfirmation } : {};
      return wrap(async () =>
        toAccount(
          await api("POST /client/payout-destinations", {
            body:
              account.kind === "mobile_money"
                ? {
                    kind: "mobile_money",
                    phone: account.phone,
                    holder_name: account.holderName.trim(),
                    ...pin,
                  }
                : {
                    kind: "bank",
                    bank_code: account.bankCode,
                    account_number: account.accountNumber,
                    holder_name: account.holderName.trim(),
                    ...pin,
                  },
          }),
          await banksOrNone(),
        ),
      );
    },

    sendCode: (accountId) =>
      wrap(async () => {
        await api("POST /client/payout-destinations/{destination_id}/code", { params: { destination_id: accountId } });
      }),

    verifyCode: (accountId, code) =>
      wrap(async () =>
        toAccount(
          await api("POST /client/payout-destinations/{destination_id}/code/verify", {
            params: { destination_id: accountId },
            body: { code },
          }),
          await banksOrNone(),
        ),
      ),

    checkNameAgain: (accountId) =>
      wrap(async () =>
        toAccount(
          await api("POST /client/payout-destinations/{destination_id}/name-check", {
            params: { destination_id: accountId },
          }),
          await banksOrNone(),
        ),
      ),

    uploadProof: (accountId, proofKind, file) =>
      wrap(async () =>
        toAccount(
          await api("POST /client/payout-destinations/{destination_id}/proof", {
            params: { destination_id: accountId },
            body: { proof_kind: proofKind, file: await uploadable(file) },
          }),
          await banksOrNone(),
        ),
      ),

    removeAccount: (accountId) =>
      wrap(async () => {
        await api("DELETE /client/payout-destinations/{destination_id}", { params: { destination_id: accountId } });
      }),

    listPayouts: () => wrap(async () => (await api("GET /client/payouts", {})).map(toPayout)),

    getPayout: (payoutId) =>
      wrap(async () => toPayout(await api("GET /client/payouts/{payout_id}", { params: { payout_id: payoutId } }))),

    resend: (payoutId, destinationId) =>
      wrap(async () =>
        toPayout(
          await api("POST /client/payouts/{payout_id}/resend", {
            params: { payout_id: payoutId },
            body: { destination_id: destinationId },
          }),
        ),
      ),

    getLimits: () => wrap(async () => toLimits(await api("GET /client/withdrawal-limits", {}))),
  };
}
