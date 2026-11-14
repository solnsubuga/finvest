/**
 * The console's view of automatic investing.
 *
 * The shapes follow backend/app/modules/autoinvest (see docs/automatic-investing.md).
 * `http.ts` reads them from `/staff/autoinvest`; `stub.ts` holds the samples.
 *
 * Amounts travel as whole-number strings in shillings, so nothing here is ever
 * a floating point number. The screen only reads: staff cannot start, retry or
 * change a run, because a run asks a client for money.
 */

export type RunStatus = "due" | "requested" | "paid" | "failed" | "missed";
export type Frequency = "weekly" | "monthly";
export type Network = "mtn" | "airtel";

export const RUN_STATUSES: readonly RunStatus[] = ["due", "requested", "paid", "failed", "missed"];

export interface Run {
  id: string;
  planId: string;
  /** The day the run was for, YYYY-MM-DD in Kampala. */
  dueOn: string;
  status: RunStatus;
  /** 1, or 2 for the one retry the day after a failed payment. */
  attempt: 1 | 2;
  /** Set on a failed first attempt that will be tried again. */
  retryOn: string | null;
  /** Why a run failed or was missed. */
  code: string | null;
  clientName: string;
  accountNumber: string;
  productName: string;
  /** Shillings, as a whole-number string. */
  amount: string;
  frequency: Frequency;
  /** 1 to 7 (Monday to Sunday) for weekly, 1 to 31 for monthly. */
  day: number;
  network: Network;
}

export interface Overview {
  plans: { active: number; paused: number };
  /** The days the runs below cover, YYYY-MM-DD. */
  from: string;
  to: string;
  runs: readonly Run[];
}

export interface AutoinvestApi {
  /** Plan counts, and every run due in the last seven days to today, newest first. */
  getOverview(today: string): Promise<Overview>;
}

export class AutoinvestError extends Error {
  constructor(readonly code: "not_connected" | "unavailable" | "forbidden") {
    super(code);
    this.name = "AutoinvestError";
  }
}

export function autoinvestErrorMessage(error: unknown): string {
  if (error instanceof AutoinvestError) {
    switch (error.code) {
      case "not_connected":
        return "The console is not connected to Finvest yet.";
      case "forbidden":
        return "Your role cannot see automatic investing.";
      case "unavailable":
        break;
    }
  }
  return "We could not reach Finvest. Try again in a moment.";
}

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedAutoinvestApi: AutoinvestApi = {
  getOverview: () => Promise.reject(new AutoinvestError("not_connected")),
};
