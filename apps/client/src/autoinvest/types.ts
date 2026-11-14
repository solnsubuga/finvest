/**
 * The contract between the automatic investing screens and the backend.
 *
 * Today the only implementation is the in-memory stub in
 * `stubAutoinvestClient.ts`. When the backend publishes its client routes, an
 * implementation backed by the generated `packages/api-client` types replaces
 * it, and the screens do not change. The backend side is described in
 * docs/automatic-investing.md.
 *
 * There is no wallet. A plan never draws on a balance: on each due day Finvest
 * sends a mobile money prompt for exactly the plan's amount, and the client
 * approves it with their mobile money PIN.
 */

import { Money, Network } from "@/payments/types";

export type Frequency = "weekly" | "monthly";
export type AtMaturity = "roll_over" | "pay_out";
export type PlanStatus = "active" | "paused" | "stopped";

/**
 * - `due`: saved, about to ask for the payment.
 * - `requested`: the prompt is on its way to the phone.
 * - `paid`: the payment was confirmed and the investment started.
 * - `failed`: the payment failed. A first failure is tried once more the next day.
 * - `missed`: the day passed without a request. Nothing is ever collected late.
 */
export type RunStatus = "due" | "requested" | "paid" | "failed" | "missed";

/** A product a plan can invest in. The rate is after tax. */
export type PlanProduct = {
  code: string;
  name: string;
  minimum: Money;
  /** "10.63", a label only. No amount is ever worked out from it. */
  ratePercent: string;
  termDays: number;
};

export type Run = {
  id: string;
  /** YYYY-MM-DD, the Kampala day it was due. */
  dueOn: string;
  status: RunStatus;
  attempt: 1 | 2;
  /** Why it failed or was missed, as the backend's code. */
  code: string | null;
};

export type Plan = {
  id: string;
  product: { code: string; name: string };
  amount: Money;
  frequency: Frequency;
  /** Weekly: 1 for Monday to 7 for Sunday. Monthly: 1 to 31. */
  day: number;
  network: Network;
  /** E.164: always the number on the client's own account. */
  payer: string;
  atMaturity: AtMaturity;
  status: PlanStatus;
  /** YYYY-MM-DD, or null when paused or stopped. */
  nextRunOn: string | null;
  /** Latest first. */
  runs: Run[];
};

export type NewPlan = {
  productCode: string;
  amount: Money;
  frequency: Frequency;
  day: number;
  network: Network;
  payer: string;
  atMaturity: AtMaturity;
  /** Made once when the form opens, so a double tap makes one plan. */
  idempotencyKey: string;
};

export type AutoinvestErrorCode =
  | "below_minimum"
  | "amount_too_large"
  | "too_many_plans"
  | "not_verified"
  | "not_found"
  | "key_reused"
  /** The plan has stopped, so it can no longer be paused or resumed. */
  | "stopped"
  /** A detail was refused for a reason the screen does not name. Nothing was saved. */
  | "invalid"
  /** A request to close the account is waiting for staff, so nothing new starts. */
  | "account_closing"
  | "unavailable";

export class AutoinvestError extends Error {
  readonly code: AutoinvestErrorCode;

  constructor(code: AutoinvestErrorCode) {
    super(code);
    this.name = "AutoinvestError";
    this.code = code;
  }
}

export interface AutoinvestClient {
  listProducts(): Promise<PlanProduct[]>;
  /** Plans that are not stopped, newest first. */
  listPlans(): Promise<Plan[]>;
  getPlan(planId: string): Promise<Plan>;
  /** Idempotent on `idempotencyKey`. The same key with different details is refused. */
  createPlan(plan: NewPlan): Promise<Plan>;
  // Pause, resume and stop carry no key: each sets a state, so sending one again changes nothing.
  pausePlan(planId: string): Promise<Plan>;
  resumePlan(planId: string): Promise<Plan>;
  stopPlan(planId: string): Promise<Plan>;
}

const messages: Record<AutoinvestErrorCode, string> = {
  account_closing: "Your request to close your account is being checked, so nothing new can start. To carry on, go to Profile, then Close account, and choose Keep my account open.",
  below_minimum: "That is less than the smallest amount this product takes.",
  amount_too_large: "A plan can collect up to UGX 5,000,000 at a time.",
  too_many_plans: "You already have 10 plans. Stop one to start another.",
  not_verified: "You can start a plan once your identity check is approved.",
  not_found: "We could not find that plan.",
  stopped: "This plan has stopped, so it cannot be changed.",
  invalid: "We could not accept that plan. Go back, check the details and try again.",
  key_reused: "This plan was already sent with other details. Go back and start a new plan.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function describeAutoinvestError(error: unknown): string {
  return error instanceof AutoinvestError ? messages[error.code] : messages.unavailable;
}
