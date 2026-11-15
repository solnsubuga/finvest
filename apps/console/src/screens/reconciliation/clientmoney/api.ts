/**
 * The client money check over `GET /staff/reconciliation/client-money`
 * (backend/app/modules/reconciliation/client_money.py, "The client money check" in
 * docs/reconciliation.md). What Finvest owes its clients, from the ledger, against what it holds
 * for them, from the statements of the last closed day and the custodian. Read only: the backend
 * works every figure out and the screen decides nothing.
 */

import type { Amount } from "../api.ts";

export type Standing = "ties" | "short" | "over";

export interface HeldPot {
  source: "iotec" | "pesapal" | "bank";
  accountReference: string;
  name: string;
  statementClosing: Amount;
  ledgerClosing: Amount;
  openDifferences: Amount;
}

export interface CheckLine {
  /** The ledger's account code, or "earnings" for Finvest's income less its costs. */
  code: string;
  name: string;
  amount: Amount;
}

export interface CustodianEvidence {
  checkId: string;
  statementDate: string;
  /** Maturities noted as not matching when it was signed off. */
  differences: number;
}

export interface ClientMoneyCheck {
  businessDate: string;
  standing: Standing;
  owed: Amount;
  interestOwed: Amount;
  pots: HeldPot[];
  onStatements: Amount;
  bills: Amount;
  /** The latest custodian check signed off on or before the day, or null if none is. */
  custodian: CustodianEvidence | null;
  /** What the bills will pay above their cost at maturity: what pays the interest owed. */
  discountToCome: Amount;
  notClientMoney: CheckLine[];
  notClientMoneyTotal: Amount;
  held: Amount;
  /** Held less owed. Below zero is a shortfall. */
  difference: Amount;
  openDifferences: Amount;
  notOnStatement: CheckLine[];
  notOnStatementTotal: Amount;
  /** What neither the open differences nor the assets off statement account for. Should be zero. */
  unexplained: Amount;
}

export interface ClientMoneyApi {
  /** The check for the last closed day, or null before any day is closed. */
  get(): Promise<ClientMoneyCheck | null>;
}

export class ClientMoneyError extends Error {
  constructor(readonly code: "forbidden" | "not_connected" | "unavailable") {
    super(code);
    this.name = "ClientMoneyError";
  }
}

export function clientMoneyErrorMessage(error: unknown): string {
  if (error instanceof ClientMoneyError && error.code === "forbidden") return "Your role cannot see reconciliation.";
  if (error instanceof ClientMoneyError && error.code === "not_connected") return "The client money check is not connected to the Finvest service yet.";
  return "We could not reach Finvest. Check your connection and try again.";
}

export const notConnectedClientMoneyApi: ClientMoneyApi = {
  get: () => Promise.reject(new ClientMoneyError("not_connected")),
};
