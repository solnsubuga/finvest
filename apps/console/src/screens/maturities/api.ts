/**
 * The console's view of the maturity run: `GET /staff/maturities` in
 * backend/app/modules/investments/api.py (see docs/investing.md, "Maturity").
 *
 * It only reads. The run closes terms by itself every morning; a term it could not close, and
 * money held because the client's choice could not be carried out, are shown for a person to
 * follow up with the client or an engineer. Amounts are whole numbers in the smallest unit.
 */

import type { Investment } from "../investments/api.ts";
import type { Amount, Currency } from "../products/api.ts";

export interface MaturityDay {
  /** `YYYY-MM-DD`, the maturity date. */
  day: string;
  currency: Currency;
  investments: number;
  principal: Amount;
  rolledOver: number;
  paidOut: number;
  held: number;
  /** Not closed yet, by what the client chose. */
  openRollOver: number;
  openPayOut: number;
}

export interface Maturities {
  /** The Kampala business day the backend worked these out on. */
  today: string;
  /** A week back to two weeks ahead, by day then currency. */
  days: readonly MaturityDay[];
  /** Past their maturity date and still running. */
  notClosed: readonly Investment[];
  /** Matured, with the money waiting for the client. */
  held: readonly Investment[];
}

export interface MaturitiesApi {
  getMaturities(): Promise<Maturities>;
}

export type MaturitiesErrorCode = "forbidden" | "not_connected" | "unavailable";

export class MaturitiesError extends Error {
  readonly code: MaturitiesErrorCode;

  constructor(code: MaturitiesErrorCode) {
    super(code);
    this.name = "MaturitiesError";
    this.code = code;
  }
}

const MESSAGES: Record<MaturitiesErrorCode, string> = {
  forbidden: "Your role does not include maturities.",
  not_connected: "Maturities are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function maturitiesErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof MaturitiesError ? error.code : "unavailable"];
}

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedMaturitiesApi: MaturitiesApi = {
  getMaturities: () => Promise.reject(new MaturitiesError("not_connected")),
};
