/**
 * The console's view of the investment catalogue: products, their rates, and
 * rate changes that one person proposes and another approves.
 *
 * `http.ts` calls the staff routes in backend/app/modules/investments/api.py
 * (`/staff/products`, `/staff/products/{code}/rates`, `/staff/rate-proposals`);
 * `stub.ts` stands in for them under `pnpm dev` and previews.
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

/** What a product change does. See docs/investing.md, "Changing a product". */
export type ProductAction = "create" | "edit" | "pause" | "open";

/**
 * A change to the catalogue that one person proposes and another approves. It names the whole
 * product as it will be once approved; the screen sets it beside the product as it is.
 */
export interface ProductChange {
  id: string;
  action: ProductAction;
  productCode: string;
  name: string;
  kind: ProductKind;
  currency: Currency;
  termDays: number | null;
  minimum: Amount;
  interestPaid: InterestPaid;
  productStatus: ProductStatus;
  /** A new product's first rate, which starts the day it is approved. Null for any other change. */
  firstRate: Rate | null;
  reason: string;
  status: ProposalStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
}

export interface NewProductDraft {
  code: string;
  name: string;
  kind: ProductKind;
  currency: Currency;
  termDays: number | null;
  minimumMinor: number;
  interestPaid: InterestPaid;
  annualRateBps: number;
  taxBps: number;
  reason: string;
}

export interface NewTermsDraft {
  productCode: string;
  name: string;
  kind: ProductKind;
  termDays: number | null;
  minimumMinor: number;
  interestPaid: InterestPaid;
  reason: string;
}

/** Who a change to a product reaches. Counts only. */
export interface ProductReach {
  /** Running investments, which keep the terms they started with. */
  runningInvestments: number;
  /** Investments waiting for payment, which keep the terms they were quoted. */
  awaitingPayment: number;
  /** Clients holding either. */
  clients: number;
  /** Open goals and plans that are not stopped: their new money takes the change. */
  openGoals: number;
  activePlans: number;
  pausedPlans: number;
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
  /** Newest first. With a status, only changes in that status. */
  listProductChanges(status?: ProposalStatus): Promise<readonly ProductChange[]>;
  proposeNewProduct(draft: NewProductDraft): Promise<ProductChange>;
  proposeNewTerms(draft: NewTermsDraft): Promise<ProductChange>;
  /** `live` opens a product to new investments, `paused` stops them. */
  proposeStatus(productCode: string, to: "live" | "paused", reason: string): Promise<ProductChange>;
  approveChange(changeId: string): Promise<ProductChange>;
  sendBackChange(changeId: string, note: string): Promise<ProductChange>;
  withdrawChange(changeId: string): Promise<ProductChange>;
  reach(productCode: string): Promise<ProductReach>;
}

export type CatalogueErrorCode =
  | "reason_required"
  | "effective_date_not_in_future"
  | "date_already_taken"
  | "nothing_changes"
  | "out_of_range"
  | "code_taken"
  | "kind_fixed"
  | "interest_paid_fixed"
  | "terms_unchanged"
  | "already_live"
  | "not_live"
  | "no_rate"
  | "currency_not_open"
  | "product_changed"
  | "invalid"
  | "conflict"
  | "not_found"
  | "forbidden"
  | "not_confirmed"
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
  code_taken: "A product with this code already exists or is waiting for approval. Pick another code.",
  kind_fixed: "Clients have already been offered this product, so its kind stays as it is.",
  interest_paid_fixed: "Clients have already been offered this product, so when interest is paid stays as it is.",
  terms_unchanged: "Nothing would change. Change at least one of the terms.",
  already_live: "This product is already open to clients.",
  not_live: "Only a product open to clients can be paused.",
  no_rate: "This product has no rate in force yet. Approve a rate before opening it.",
  currency_not_open: "Finvest cannot take payments or pay out in this currency yet, so the product stays paused.",
  product_changed: "The product changed after this was proposed. Propose it again against the product as it is now.",
  invalid: "Finvest did not accept these details. Check them and try again.",
  conflict: "Someone else has already decided this change. The list has been reloaded.",
  not_found: "This change no longer exists.",
  forbidden: "A second person has to approve this, or you are not allowed to make this change.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
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
  listProductChanges: notConnected,
  proposeNewProduct: notConnected,
  proposeNewTerms: notConnected,
  proposeStatus: notConnected,
  approveChange: notConnected,
  sendBackChange: notConnected,
  withdrawChange: notConnected,
  reach: notConnected,
};
