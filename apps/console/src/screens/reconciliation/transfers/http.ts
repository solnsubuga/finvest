/** Transfers between pots over `/staff/reconciliation/pot-transfers` and the pot list. */

import { ApiError, type Http } from "@/api/http.ts";
import {
  PotTransfersError,
  type Amount,
  type Currency,
  type PotSource,
  type PotTransfer,
  type PotTransfersApi,
  type PotTransfersErrorCode,
} from "./api.ts";

interface PotJson {
  source: PotSource;
  account_reference: string;
  name: string;
  currency: Currency;
}

interface TransferJson {
  id: string;
  from_pot: { source: PotSource; account_reference: string; name: string };
  to_pot: { source: PotSource; account_reference: string; name: string };
  sent: Amount;
  received: Amount;
  fee: Amount;
  value_date: string;
  provider_reference: string;
  status: PotTransfer["status"];
  proposed_by: string;
  proposed_at: string;
}

const pot = (json: { source: PotSource; account_reference: string; name: string }) => ({
  source: json.source,
  accountReference: json.account_reference,
  name: json.name,
});

export function toTransfer(json: TransferJson): PotTransfer {
  return {
    id: json.id,
    fromPot: pot(json.from_pot),
    toPot: pot(json.to_pot),
    sent: json.sent,
    received: json.received,
    fee: json.fee,
    valueDate: json.value_date,
    providerReference: json.provider_reference,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
  };
}

const NAMED: ReadonlySet<string> = new Set<PotTransfersErrorCode>([
  "pot_unknown",
  "transfer_same_pot",
  "currency_not_supported",
  "amount_not_positive",
  "transfer_received_more_than_sent",
  "transfer_fee_not_allowed",
  "transfer_in_the_future",
  "transfer_in_a_closed_day",
  "transfer_already_recorded",
  "transfer_needs_second_person",
  "transfer_already_decided",
  "transfer_more_than_the_pot_holds",
]);

export function toPotTransfersError(error: unknown): PotTransfersError {
  if (error instanceof PotTransfersError) return error;
  if (!(error instanceof ApiError)) return new PotTransfersError("unavailable");
  const named = error.fieldCode;
  if (named && NAMED.has(named)) return new PotTransfersError(named as PotTransfersErrorCode);
  switch (error.code) {
    case "validation_failed":
      return new PotTransfersError("invalid");
    case "not_found":
      return new PotTransfersError("not_found");
    case "confirm_it_is_you":
      return new PotTransfersError("not_confirmed");
    case "forbidden":
      return new PotTransfersError("forbidden");
    case "conflict":
      return new PotTransfersError("refused");
    default:
      return new PotTransfersError("unavailable");
  }
}

export function createHttpPotTransfersApi(http: Http): PotTransfersApi {
  const base = "/staff/reconciliation/pot-transfers";
  async function call<T>(run: () => Promise<T>): Promise<T> {
    try {
      return await run();
    } catch (error) {
      throw toPotTransfersError(error);
    }
  }
  return {
    listPots: () =>
      call(async () =>
        (await http.request<PotJson[]>("GET", "/staff/reconciliation/pots")).map((json) => ({ ...pot(json), currency: json.currency })),
      ),
    listWaiting: () => call(async () => (await http.request<TransferJson[]>("GET", base)).map(toTransfer)),
    record: (key, transfer) =>
      call(async () => {
        await http.request(
          "POST",
          base,
          {
            from_pot: { source: transfer.from.source, account_reference: transfer.from.accountReference },
            to_pot: { source: transfer.to.source, account_reference: transfer.to.accountReference },
            sent: transfer.sent,
            received: transfer.received,
            value_date: transfer.valueDate,
            provider_reference: transfer.providerReference,
            reason: transfer.reason,
          },
          { idempotencyKey: key },
        );
      }),
    approve: (key, id) =>
      call(async () => {
        await http.request("POST", `${base}/${encodeURIComponent(id)}/approve`, undefined, { idempotencyKey: key });
      }),
    decline: (key, id, reason) =>
      call(async () => {
        await http.request("POST", `${base}/${encodeURIComponent(id)}/decline`, { reason }, { idempotencyKey: key });
      }),
  };
}
