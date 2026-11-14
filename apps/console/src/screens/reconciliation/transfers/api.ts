/**
 * The console's view of transfers between Finvest's own pots, over the staff routes in
 * backend/app/modules/reconciliation/transfers_api.py (see "Transfers between pots" in
 * docs/reconciliation.md).
 *
 * Finvest moves its own money between pots, such as a sweep of collected mobile money from an
 * ioTec wallet to the trust bank account. One member of staff records a transfer that has been
 * made, as the provider or bank shows it. A different one approves it, which posts it to the
 * ledger, or declines it. The backend checks every rule. The screen decides nothing.
 */

import type { Amount, Currency } from "../api.ts";

export type { Amount, Currency };

export type PotSource = "iotec" | "bank";

export interface Pot {
  source: PotSource;
  accountReference: string;
  name: string;
  currency: Currency;
}

export interface PotTransfer {
  id: string;
  fromPot: { source: PotSource; accountReference: string; name: string };
  toPot: { source: PotSource; accountReference: string; name: string };
  sent: Amount;
  received: Amount;
  /** What the provider kept: sent minus received. */
  fee: Amount;
  /** `YYYY-MM-DD`, the day the money moved. */
  valueDate: string;
  providerReference: string;
  status: "proposed" | "approved" | "declined";
  proposedBy: string;
  proposedAt: string;
}

export interface TransferRecord {
  from: Pick<Pot, "source" | "accountReference">;
  to: Pick<Pot, "source" | "accountReference">;
  sent: Amount;
  received: Amount;
  valueDate: string;
  providerReference: string;
  reason: string;
}

/** Recording, approving and declining all take an idempotency key. */
export interface PotTransfersApi {
  listPots(): Promise<Pot[]>;
  /** Transfers waiting for a second person, oldest first. */
  listWaiting(): Promise<PotTransfer[]>;
  record(key: string, transfer: TransferRecord): Promise<void>;
  approve(key: string, transferId: string): Promise<void>;
  decline(key: string, transferId: string, reason: string): Promise<void>;
}

export type PotTransfersErrorCode =
  | "not_connected"
  | "unavailable"
  | "forbidden"
  | "refused"
  | "not_found"
  | "invalid"
  | "not_confirmed"
  | "pot_unknown"
  | "transfer_same_pot"
  | "currency_not_supported"
  | "amount_not_positive"
  | "transfer_received_more_than_sent"
  | "transfer_fee_not_allowed"
  | "transfer_in_the_future"
  | "transfer_in_a_closed_day"
  | "transfer_already_recorded"
  | "transfer_needs_second_person"
  | "transfer_already_decided"
  | "transfer_more_than_the_pot_holds";

export class PotTransfersError extends Error {
  constructor(readonly code: PotTransfersErrorCode) {
    super(code);
    this.name = "PotTransfersError";
  }
}

const MESSAGES: Record<PotTransfersErrorCode, string> = {
  not_connected: "The console is not connected to Finvest yet.",
  unavailable: "We could not reach Finvest. Try again in a moment.",
  forbidden: "Your role cannot do this.",
  refused: "Finvest refused this as things now stand. The list has been refreshed.",
  not_found: "This transfer no longer exists. The list has been refreshed.",
  invalid: "Finvest did not accept what was entered. Check each field.",
  not_confirmed: "Nothing was done, because you did not confirm it is you.",
  pot_unknown: "Finvest does not know one of those pots.",
  transfer_same_pot: "Choose two different pots.",
  currency_not_supported: "Both pots and both amounts must be in the same currency.",
  amount_not_positive: "Enter amounts above zero.",
  transfer_received_more_than_sent: "What arrived cannot be more than what was sent.",
  transfer_fee_not_allowed: "A transfer out of the trust bank account cannot carry a fee yet. What arrived must equal what was sent.",
  transfer_in_the_future: "The day the money moved cannot be after today.",
  transfer_in_a_closed_day: "That day is closed. A transfer cannot be recorded in a closed day.",
  transfer_already_recorded: "A transfer with that reference from that pot is already recorded.",
  transfer_needs_second_person: "You recorded this transfer, so a different person must approve or decline it.",
  transfer_already_decided: "This transfer has already been decided. The list has been refreshed.",
  transfer_more_than_the_pot_holds: "The books say the sending pot never held that much. Check the amount and the statements.",
};

export function potTransfersErrorMessage(error: unknown): string {
  return error instanceof PotTransfersError ? MESSAGES[error.code] : MESSAGES.unavailable;
}

const notConnected = () => Promise.reject(new PotTransfersError("not_connected"));

export const notConnectedPotTransfersApi: PotTransfersApi = {
  listPots: notConnected,
  listWaiting: notConnected,
  record: notConnected,
  approve: notConnected,
  decline: notConnected,
};
