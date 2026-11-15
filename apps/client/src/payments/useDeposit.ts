import { useCallback } from "react";

import { usePayments } from "./PaymentsProvider";
import { BankTransfer, CardPayment, Deposit, isFinal, PaymentError } from "./types";
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

export type CardPaymentView = { payment: CardPayment | null; missing: boolean; stale: boolean };

const cardDone = (payment: CardPayment) => isFinal(payment.status);

/** Reads a card payment until Pesapal's own record says it is paid or not. */
export function useCardPayment(paymentId: string): CardPaymentView {
  const { client } = usePayments();
  const read = useCallback(async () => {
    if (!client.getCardPayment) throw new PaymentError("not_found");
    return client.getCardPayment(paymentId);
  }, [client, paymentId]);
  const { value, missing, stale } = usePolled(read, cardDone);
  return { payment: value, missing, stale };
}
