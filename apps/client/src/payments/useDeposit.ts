import { useCallback } from "react";

import { usePayments } from "./PaymentsProvider";
import { BankTransfer, Deposit, isFinal } from "./types";
import { usePolled } from "./usePolled";

export type DepositView = { deposit: Deposit | null; missing: boolean; stale: boolean };

const depositDone = (deposit: Deposit) => isFinal(deposit.status);
const transferDone = (transfer: BankTransfer) => transfer.status !== "checking";

/**
 * Reads a mobile money payment until it reaches a final state. It never asks
 * for the money again: a payment with no clear answer is settled by the server
 * asking the network, not by the app.
 */
export function useDeposit(depositId: string): DepositView {
  const { client } = usePayments();
  const read = useCallback(() => client.getDeposit(depositId), [client, depositId]);
  const { value, missing, stale } = usePolled(read, depositDone);
  return { deposit: value, missing, stale };
}

export type BankTransferView = { transfer: BankTransfer | null; missing: boolean; stale: boolean };

/** Reads a bank transfer until staff have matched it or said why they could not. */
export function useBankTransfer(transferId: string): BankTransferView {
  const { client } = usePayments();
  const read = useCallback(() => client.getBankTransfer(transferId), [client, transferId]);
  const { value, missing, stale } = usePolled(read, transferDone);
  return { transfer: value, missing, stale };
}
