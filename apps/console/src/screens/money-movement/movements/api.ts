/**
 * Every payment in and payout, newest first, over `GET /staff/money-movements`
 * (backend/app/modules/console/movements.py). Read only: the screen decides nothing.
 */

export interface Amount {
  minor: number;
  currency: "UGX" | "USD";
}

export type MovementFilter = "all" | "deposits" | "payouts" | "failed" | "unmatched";

export interface Movement {
  kind: "deposit" | "payout";
  id: string;
  clientId: string;
  /** The name on the client's approved ID, or null until it is approved. */
  clientName: string | null;
  /** mtn, airtel or bank_transfer for a deposit; mobile_money or bank for a payout. */
  channel: string;
  amount: Amount;
  status: string;
  failureCode: string | null;
  createdAt: string;
  /** When it ended, or null while it is still open. */
  endedAt: string | null;
}

export interface MovementPage {
  movements: Movement[];
  /** Pass back as `after` for the next page. Null on the last page. */
  nextAfter: string | null;
}

export interface MovementsApi {
  list(show: MovementFilter, after: string | null): Promise<MovementPage>;
}

export class MovementsError extends Error {
  constructor(readonly code: "forbidden" | "unavailable") {
    super(code);
    this.name = "MovementsError";
  }
}

export function movementsErrorMessage(error: unknown): string {
  if (error instanceof MovementsError && error.code === "forbidden") return "Your role cannot see money movements.";
  return "Finvest could not be reached. Check your connection and try again.";
}

export const notConnectedMovementsApi: MovementsApi = {
  list: () => Promise.reject(new MovementsError("unavailable")),
};
