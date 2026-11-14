/**
 * Products, rates and rate changes over the real API (backend/app/modules/investments/api.py).
 *
 * Approving a rate needs a fresh second step: the API answers 403 `confirm_it_is_you`, the
 * shared dialog asks, and createHttp sends the approval once more. Closing the dialog leaves
 * the refusal standing, and it becomes `not_confirmed` here.
 */

import type { ProductChangeOut, ProductReachOut, ProposalOut, RateOut, StaffProductOut } from "@finvest/api-client/staff";
import { ApiError, apiBaseUrl, createHttp, type Http } from "@/api/http.ts";
import {
  CatalogueError,
  notConnectedCatalogueApi,
  type CatalogueApi,
  type CatalogueErrorCode,
  type Product,
  type ProductChange,
  type ProductReach,
  type Proposal,
  type Rate,
} from "./api.ts";

export function toRate(json: RateOut): Rate {
  return {
    effectiveFrom: json.effective_from,
    annualRateBps: json.annual_rate_bps,
    taxBps: json.tax_bps,
    clientRatePercent: json.client_rate_percent,
  };
}

export function toProduct(json: StaffProductOut): Product {
  return {
    id: json.id,
    code: json.code,
    name: json.name,
    kind: json.kind,
    currency: json.currency,
    termDays: json.term_days,
    minimum: { minor: json.minimum_minor, currency: json.currency },
    interestPaid: json.interest_paid,
    status: json.status,
    rate: json.rate ? toRate(json.rate) : null,
    nextRate: json.next_rate ? toRate(json.next_rate) : null,
  };
}

export function toProposal(json: ProposalOut): Proposal {
  return {
    id: json.id,
    productCode: json.product_code,
    rate: toRate(json.rate),
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
  };
}

export function toProductChange(json: ProductChangeOut): ProductChange {
  return {
    id: json.id,
    action: json.action,
    productCode: json.product_code,
    name: json.name,
    kind: json.kind,
    currency: json.currency,
    termDays: json.term_days,
    minimum: { minor: json.minimum_minor, currency: json.currency },
    interestPaid: json.interest_paid,
    productStatus: json.product_status,
    firstRate: json.first_rate ? toRate(json.first_rate) : null,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
  };
}

export function toReach(json: ProductReachOut): ProductReach {
  return {
    runningInvestments: json.running_investments,
    awaitingPayment: json.awaiting_payment,
    clients: json.clients,
    openGoals: json.open_goals,
    activePlans: json.active_plans,
    pausedPlans: json.paused_plans,
  };
}

/**
 * The field problems the backend names for a rate change (`_FIELDS` in investments/catalogue.py)
 * and a product change (`_FIELDS` in investments/product_changes.py).
 */
const FIELD_CODES: readonly CatalogueErrorCode[] = [
  "reason_required",
  "effective_date_not_in_future",
  "date_already_taken",
  "nothing_changes",
  "out_of_range",
  "code_taken",
  "kind_fixed",
  "interest_paid_fixed",
  "already_live",
  "not_live",
  "no_rate",
  "currency_not_open",
  "product_changed",
];

export function toCatalogueError(error: unknown): CatalogueError {
  if (error instanceof CatalogueError) return error;
  if (!(error instanceof ApiError)) return new CatalogueError("unavailable");
  switch (error.code) {
    case "validation_failed": {
      // A product change that alters nothing names the whole product, not the rate.
      if (error.fieldCode === "nothing_changes" && error.fields[0]?.field_path[0] === "product") {
        return new CatalogueError("terms_unchanged");
      }
      const field = FIELD_CODES.find((code) => code === error.fieldCode);
      return new CatalogueError(field ?? "invalid");
    }
    // Already decided, or another change for the product is already waiting.
    case "conflict":
      return new CatalogueError("conflict");
    case "not_found":
      return new CatalogueError("not_found");
    // Without the permission, or the proposer deciding their own change.
    case "forbidden":
      return new CatalogueError("forbidden");
    // The person closed the "confirm it is you" dialog, so nothing was approved.
    case "confirm_it_is_you":
      return new CatalogueError("not_confirmed");
    default:
      return new CatalogueError("unavailable");
  }
}

export function createHttpCatalogueApi(http: Http): CatalogueApi {
  const proposal = (id: string, action: string) => `/staff/rate-proposals/${encodeURIComponent(id)}/${action}`;
  const change = (id: string, action: string) => `/staff/product-changes/${encodeURIComponent(id)}/${action}`;

  async function call<T>(work: () => Promise<T>): Promise<T> {
    try {
      return await work();
    } catch (error) {
      throw toCatalogueError(error);
    }
  }

  return {
    listProducts: () => call(async () => (await http.request<StaffProductOut[]>("GET", "/staff/products")).map(toProduct)),
    rateHistory: (productCode) =>
      call(async () =>
        (await http.request<RateOut[]>("GET", `/staff/products/${encodeURIComponent(productCode)}/rates`)).map(toRate),
      ),
    listProposals: (status) =>
      call(async () => {
        const query = status ? `?${new URLSearchParams({ status }).toString()}` : "";
        return (await http.request<ProposalOut[]>("GET", `/staff/rate-proposals${query}`)).map(toProposal);
      }),
    propose: (input) =>
      call(async () =>
        toProposal(
          await http.request<ProposalOut>("POST", "/staff/rate-proposals", {
            product_code: input.productCode,
            annual_rate_bps: input.annualRateBps,
            tax_bps: input.taxBps,
            effective_from: input.effectiveFrom,
            reason: input.reason,
          }),
        ),
      ),
    approve: (id) => call(async () => toProposal(await http.request<ProposalOut>("POST", proposal(id, "approve")))),
    sendBack: (id, note) => call(async () => toProposal(await http.request<ProposalOut>("POST", proposal(id, "send-back"), { note }))),
    withdraw: (id) => call(async () => toProposal(await http.request<ProposalOut>("POST", proposal(id, "withdraw")))),
    listProductChanges: (status) =>
      call(async () => {
        const query = status ? `?${new URLSearchParams({ status }).toString()}` : "";
        return (await http.request<ProductChangeOut[]>("GET", `/staff/product-changes${query}`)).map(toProductChange);
      }),
    proposeNewProduct: (draft) =>
      call(async () =>
        toProductChange(
          await http.request<ProductChangeOut>("POST", "/staff/product-changes/new-product", {
            code: draft.code,
            name: draft.name,
            kind: draft.kind,
            currency: draft.currency,
            term_days: draft.termDays,
            minimum_minor: draft.minimumMinor,
            interest_paid: draft.interestPaid,
            annual_rate_bps: draft.annualRateBps,
            tax_bps: draft.taxBps,
            reason: draft.reason,
          }),
        ),
      ),
    proposeNewTerms: (draft) =>
      call(async () =>
        toProductChange(
          await http.request<ProductChangeOut>("POST", "/staff/product-changes/new-terms", {
            product_code: draft.productCode,
            name: draft.name,
            kind: draft.kind,
            term_days: draft.termDays,
            minimum_minor: draft.minimumMinor,
            interest_paid: draft.interestPaid,
            reason: draft.reason,
          }),
        ),
      ),
    proposeStatus: (productCode, to, reason) =>
      call(async () =>
        toProductChange(
          await http.request<ProductChangeOut>("POST", "/staff/product-changes/status", { product_code: productCode, to, reason }),
        ),
      ),
    approveChange: (id) => call(async () => toProductChange(await http.request<ProductChangeOut>("POST", change(id, "approve")))),
    sendBackChange: (id, note) =>
      call(async () => toProductChange(await http.request<ProductChangeOut>("POST", change(id, "send-back"), { note }))),
    withdrawChange: (id) => call(async () => toProductChange(await http.request<ProductChangeOut>("POST", change(id, "withdraw")))),
    reach: (productCode) =>
      call(async () => toReach(await http.request<ProductReachOut>("GET", `/staff/products/${encodeURIComponent(productCode)}/reach`))),
  };
}

/** The real API when the build names one, otherwise a catalogue that says it is not connected. */
export function connectedCatalogueApi(): CatalogueApi {
  const base = apiBaseUrl();
  return base ? createHttpCatalogueApi(createHttp(base)) : notConnectedCatalogueApi;
}
