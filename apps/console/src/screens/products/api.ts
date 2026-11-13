/**
 * The console's view of the investment catalogue: products, their rates, and
 * rate changes that one person proposes and another approves.
 *
 * A stand-in for the generated client in packages/api-client, like AuthApi.
 * The shapes follow the staff routes in backend/app/modules/investments/api.py
 * (`/staff/products`, `/staff/products/{code}/rates`, `/staff/rate-proposals`).
 * When the generated client exists, an implementation that calls them
 * replaces the stub and the screens do not change.
 *
 * Staff see rates before tax and the tax. Clients never do: they see only
 * `clientRatePercent`, the rate after tax, which the backend works out.
 */

export type Currency = "UGX" | "USD";

/** An integer in the smallest unit: whole shillings for UGX, cents for USD. */
export interface Amount {
  minor: number;
  currency: Currency;
}

export type ProductKind = "treasury_bill" | "fixed_income";
export type ProductStatus = "draft" | "live" | "paused" | "closed";
export type InterestPaid = "at_maturity" | "monthly";
export type ProposalStatus = "proposed" | "approved" | "sent_back" | "withdrawn";

export interface Rate {
  /** A calendar date. The rate applies to investments reviewed on or after it. */
  effectiveFrom: string;
  /** Yearly rate before tax, in hundredths of a percent: 1250 is 12.5%. */
  annualRateBps: number;
  /** Withholding tax on interest, in hundredths of a percent. */
  taxBps: number;
  /** What a client sees, after tax, as the backend rounds it: "10.63". */
  clientRatePercent: string;
}

export interface Product {
  id: string;
  code: string;
  name: string;
  kind: ProductKind;
  currency: Currency;
  /** Null for a product without a fixed term. */
  termDays: number | null;
  minimum: Amount;
  interestPaid: InterestPaid;
  status: ProductStatus;
  /** The rate in force today. Null before the first one starts. */
  rate: Rate | null;
  /** An approved rate that starts later. */
  nextRate: Rate | null;
}

export interface Proposal {
  id: string;
  productCode: string;
  rate: Rate;
  reason: string;
  status: ProposalStatus;
  /** A staff ID. The screen compares it with the signed-in person's own. */
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
}

export interface NewProposal {
  productCode: string;
  annualRateBps: number;
  taxBps: number;
  effectiveFrom: string;
  reason: string;
}

export interface CatalogueApi {
  listProducts(): Promise<readonly Product[]>;
  /** Every rate a product has had or will have, newest start first. */
  rateHistory(productCode: string): Promise<readonly Rate[]>;
  /** Newest first. With a status, only proposals in that status. */
  listProposals(status?: ProposalStatus): Promise<readonly Proposal[]>;
  propose(proposal: NewProposal): Promise<Proposal>;
  approve(proposalId: string): Promise<Proposal>;
  sendBack(proposalId: string, note: string): Promise<Proposal>;
  withdraw(proposalId: string): Promise<Proposal>;
}

export type CatalogueErrorCode =
  | "reason_required"
  | "effective_date_not_in_future"
  | "date_already_taken"
  | "nothing_changes"
  | "out_of_range"
  | "conflict"
  | "not_found"
  | "forbidden"
  | "not_connected"
  | "unavailable";

export class CatalogueError extends Error {
  readonly code: CatalogueErrorCode;

  constructor(code: CatalogueErrorCode) {
    super(code);
    this.name = "CatalogueError";
    this.code = code;
  }
}

const MESSAGES: Record<CatalogueErrorCode, string> = {
  reason_required: "Say why the rate is changing. The reason goes in the audit trail.",
  effective_date_not_in_future: "A new rate starts on a future day, so nobody's quote changes under them.",
  date_already_taken: "This product already has a rate starting on that day. Pick another day.",
  nothing_changes: "That is the rate already in force. Change the rate or the tax.",
  out_of_range: "The rate must be between 0.01% and 50%, and the tax below 100%.",
  conflict: "Someone else has already decided this change. The list has been reloaded.",
  not_found: "This rate change no longer exists.",
  forbidden: "A second person has to approve this, or you are not allowed to change rates.",
  not_connected: "Products and rates are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function catalogueErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof CatalogueError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new CatalogueError("not_connected"));

/** Used in builds with no backend client and the stub turned off. */
export const notConnectedCatalogueApi: CatalogueApi = {
  listProducts: notConnected,
  rateHistory: notConnected,
  listProposals: notConnected,
  propose: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
};
