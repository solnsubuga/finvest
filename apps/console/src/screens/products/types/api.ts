/**
 * The console's view of product types: the kinds of product Finvest offers, each built on one of
 * the return models the code supports, and the changes one person proposes and another approves.
 * See docs/product-types.md.
 *
 * `http.ts` calls the staff routes in backend/app/modules/product_types/api.py
 * (`/staff/product-types`, `/staff/product-type-changes`); `stub.ts` stands in for them under
 * `pnpm dev` and previews.
 */

import type { ProposalStatus } from "../api.ts";

/** How a product earns. A new way of earning needs code first, so staff pick one of these. */
export type ReturnModel = "treasury_bill" | "fixed_income" | "treasury_bond";
export type TypeStatus = "active" | "retired";
export type TypeAction = "create" | "edit" | "retire" | "restore";

export interface ProductType {
  code: string;
  /** The name staff pick when adding a product. */
  name: string;
  model: ReturnModel;
  description: string;
  /** The tax a new product of this type starts with, in hundredths of a percent. */
  defaultTaxBps: number;
  /** A retired type keeps its products but no new ones can be added. */
  status: TypeStatus;
  /** One of the three Finvest started with. */
  builtIn: boolean;
  /** Products and bond issues of this type, of any status. */
  inUse: number;
}

export interface ReturnModelInfo {
  model: ReturnModel;
  description: string;
}

export interface ProductTypeBook {
  types: readonly ProductType[];
  /** What a new type can be built on. */
  models: readonly ReturnModelInfo[];
}

export interface TypeTerms {
  name: string;
  description: string;
  defaultTaxBps: number;
}

export interface ProductTypeChange {
  id: string;
  action: TypeAction;
  code: string;
  model: ReturnModel;
  /** The type as it will be. Null for retiring or restoring. */
  terms: TypeTerms | null;
  reason: string;
  status: ProposalStatus;
  proposedBy: string;
  proposedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
}

export interface NewTypeDraft extends TypeTerms {
  code: string;
  model: ReturnModel;
  reason: string;
}

export interface ProductTypesApi {
  listTypes(): Promise<ProductTypeBook>;
  /** Newest first. With a status, only changes in that status. */
  listChanges(status?: ProposalStatus): Promise<readonly ProductTypeChange[]>;
  proposeType(draft: NewTypeDraft): Promise<ProductTypeChange>;
  proposeEdit(code: string, terms: TypeTerms, reason: string): Promise<ProductTypeChange>;
  /** `retired` stops new products of the type, `active` allows them again. */
  proposeStatus(code: string, to: TypeStatus, reason: string): Promise<ProductTypeChange>;
  approve(changeId: string): Promise<ProductTypeChange>;
  sendBack(changeId: string, note: string): Promise<ProductTypeChange>;
  withdraw(changeId: string): Promise<ProductTypeChange>;
}

export type ProductTypesErrorCode =
  | "invalid_code"
  | "invalid_name"
  | "invalid_description"
  | "invalid_tax"
  | "code_taken"
  | "name_taken"
  | "reason_required"
  | "note_required"
  | "nothing_changes"
  | "already_retired"
  | "not_retired"
  | "stale"
  | "invalid"
  | "conflict"
  | "not_found"
  | "forbidden"
  | "not_confirmed"
  | "not_connected"
  | "unavailable";

export class ProductTypesError extends Error {
  readonly code: ProductTypesErrorCode;

  constructor(code: ProductTypesErrorCode) {
    super(code);
    this.name = "ProductTypesError";
    this.code = code;
  }
}

const MESSAGES: Record<ProductTypesErrorCode, string> = {
  invalid_code: "Use capital letters, digits and hyphens for the code, such as COMPANY-NOTE.",
  invalid_name: "Give the type a name of up to 60 characters.",
  invalid_description: "Say in up to 300 characters what a product of this type is.",
  invalid_tax: "Type the usual tax as a percentage below 100, such as 15.",
  code_taken: "A type with this code already exists or is waiting for approval. Pick another code.",
  name_taken: "A type with this name already exists or is waiting for approval. Pick another name.",
  reason_required: "Say why. The reason goes in the audit trail.",
  note_required: "Say why it is going back.",
  nothing_changes: "Nothing would change. Change the name, the description or the usual tax.",
  already_retired: "This type is already retired.",
  not_retired: "This type is already in use for new products.",
  stale: "The type changed after this was proposed. Propose it again.",
  invalid: "Finvest did not accept these details. Check them and try again.",
  conflict: "Someone else has decided this change, or another change for this type is waiting. The list has been reloaded.",
  not_found: "This type or change no longer exists.",
  forbidden: "A second person has to approve this, or you are not allowed to make this change.",
  not_confirmed: "Not done. Confirm it is you with your passkey or authenticator app, then try again.",
  not_connected: "Product types are not connected to the Finvest service yet.",
  unavailable: "We could not reach Finvest. Check your connection and try again.",
};

export function productTypesErrorMessage(error: unknown): string {
  return MESSAGES[error instanceof ProductTypesError ? error.code : "unavailable"];
}

const notConnected = () => Promise.reject(new ProductTypesError("not_connected"));

export const notConnectedProductTypesApi: ProductTypesApi = {
  listTypes: notConnected,
  listChanges: notConnected,
  proposeType: notConnected,
  proposeEdit: notConnected,
  proposeStatus: notConnected,
  approve: notConnected,
  sendBack: notConnected,
  withdraw: notConnected,
};

export const MODEL_LABELS: Record<ReturnModel, string> = {
  treasury_bill: "Term and rate, paid at maturity",
  fixed_income: "Term and rate, paid at maturity or monthly",
  treasury_bond: "Bank of Uganda bond with coupons",
};

export const TYPE_STATUS_LABELS: Record<TypeStatus, string> = { active: "In use", retired: "Retired" };

export const TYPE_ACTION_LABELS: Record<TypeAction, string> = {
  create: "New product type",
  edit: "Type changes",
  retire: "Retire type",
  restore: "Restore type",
};
