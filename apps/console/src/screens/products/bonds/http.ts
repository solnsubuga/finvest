/**
 * Bond issues and their changes over the real API (backend/app/modules/bonds/api.py).
 *
 * Approving needs a fresh second step: the API answers 403 `confirm_it_is_you`, the shared
 * dialog asks, and createHttp sends the approval once more. Closing the dialog leaves the
 * refusal standing, and it becomes `not_confirmed` here.
 */

import type { BondChangeOut, BondTermsOut, StaffBondOut } from "@finvest/api-client/staff";
import { ApiError, apiBaseUrl, createHttp, type Http } from "@/api/http.ts";
import { BondsError, notConnectedBondsApi, type Bond, type BondChange, type BondsApi, type BondsErrorCode, type BondTerms } from "./api.ts";

export function toBond(json: StaffBondOut): Bond {
  return {
    code: json.code,
    isin: json.isin,
    name: json.name,
    currency: json.currency,
    couponBps: json.coupon_bps,
    taxBps: json.tax_bps,
    usualTaxBps: json.usual_tax_bps,
    issueDate: json.issue_date,
    maturityDate: json.maturity_date,
    years: json.years,
    minimumFace: { minor: json.minimum_face_minor, currency: json.currency },
    faceStep: { minor: json.face_step_minor, currency: json.currency },
    status: json.status,
    price: json.price,
    pricedAt: json.priced_at,
  };
}

function toTerms(json: BondTermsOut): BondTerms {
  return {
    isin: json.isin,
    name: json.name,
    currency: json.currency,
    couponBps: json.coupon_bps,
    taxBps: json.tax_bps,
    issueDate: json.issue_date,
    maturityDate: json.maturity_date,
    minimumFace: { minor: json.minimum_face_minor, currency: json.currency },
    faceStep: { minor: json.face_step_minor, currency: json.currency },
  };
}

export function toBondChange(json: BondChangeOut): BondChange {
  return {
    id: json.id,
    action: json.action,
    code: json.code,
    terms: json.terms ? toTerms(json.terms) : null,
    price: json.price,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
  };
}

/** The field problems the backend names (`_FIELDS` in bonds/catalogue.py). */
const FIELD_CODES: readonly BondsErrorCode[] = [
  "reason_required",
  "code_taken",
  "isin_taken",
  "same_price",
  "out_of_range",
  "already_live",
  "not_live",
  "no_price",
  "matured",
  "currency_not_open",
  "stale",
];

export function toBondsError(error: unknown): BondsError {
  if (error instanceof BondsError) return error;
  if (!(error instanceof ApiError)) return new BondsError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new BondsError(FIELD_CODES.find((code) => code === error.fieldCode) ?? "invalid");
    case "conflict":
      return new BondsError("conflict");
    case "not_found":
      return new BondsError("not_found");
    case "forbidden":
      return new BondsError("forbidden");
    case "confirm_it_is_you":
      return new BondsError("not_confirmed");
    default:
      return new BondsError("unavailable");
  }
}

export function createHttpBondsApi(http: Http): BondsApi {
  const change = (id: string, action: string) => `/staff/bond-changes/${encodeURIComponent(id)}/${action}`;

  async function call<T>(work: () => Promise<T>): Promise<T> {
    try {
      return await work();
    } catch (error) {
      throw toBondsError(error);
    }
  }

  const post = (path: string, body?: unknown) => call(async () => toBondChange(await http.request<BondChangeOut>("POST", path, body)));

  return {
    listBonds: () => call(async () => (await http.request<StaffBondOut[]>("GET", "/staff/bonds")).map(toBond)),
    listChanges: (status) =>
      call(async () => {
        const query = status ? `?${new URLSearchParams({ status }).toString()}` : "";
        return (await http.request<BondChangeOut[]>("GET", `/staff/bond-changes${query}`)).map(toBondChange);
      }),
    proposeBond: (draft) =>
      post("/staff/bond-changes/new-issue", {
        code: draft.code,
        isin: draft.isin,
        name: draft.name,
        currency: "UGX",
        coupon_bps: draft.couponBps,
        tax_bps: draft.taxBps,
        issue_date: draft.issueDate,
        maturity_date: draft.maturityDate,
        minimum_face_minor: draft.minimumFaceMinor,
        face_step_minor: draft.faceStepMinor,
        price: draft.price,
        reason: draft.reason,
      }),
    proposePrice: (code, price, reason) => post("/staff/bond-changes/price", { code, price, reason }),
    proposeStatus: (code, to, reason) => post("/staff/bond-changes/status", { code, to, reason }),
    approve: (id) => post(change(id, "approve")),
    sendBack: (id, note) => post(change(id, "send-back"), { note }),
    withdraw: (id) => post(change(id, "withdraw")),
  };
}

export function connectedBondsApi(): BondsApi {
  const base = apiBaseUrl();
  return base ? createHttpBondsApi(createHttp(base)) : notConnectedBondsApi;
}
