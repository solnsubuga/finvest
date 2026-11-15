/**
 * Product types and their changes over the real API (backend/app/modules/product_types/api.py).
 *
 * Approving needs a fresh second step: the API answers 403 `confirm_it_is_you`, the shared
 * dialog asks, and createHttp sends the approval once more. Closing the dialog leaves the
 * refusal standing, and it becomes `not_confirmed` here.
 */

import type { ProductTypeChangeOut, ProductTypeOut, ProductTypesOut } from "@finvest/api-client/staff";
import { ApiError, apiBaseUrl, createHttp, type Http } from "@/api/http.ts";
import {
  notConnectedProductTypesApi,
  ProductTypesError,
  type ProductType,
  type ProductTypeChange,
  type ProductTypesApi,
  type ProductTypesErrorCode,
} from "./api.ts";

export function toProductType(json: ProductTypeOut): ProductType {
  return {
    code: json.code,
    name: json.name,
    model: json.model,
    description: json.description,
    defaultTaxBps: json.default_tax_bps,
    status: json.status,
    builtIn: json.built_in,
    inUse: json.in_use,
  };
}

export function toProductTypeChange(json: ProductTypeChangeOut): ProductTypeChange {
  return {
    id: json.id,
    action: json.action,
    code: json.code,
    model: json.model,
    terms:
      json.name !== null && json.description !== null && json.default_tax_bps !== null
        ? { name: json.name, description: json.description, defaultTaxBps: json.default_tax_bps }
        : null,
    reason: json.reason,
    status: json.status,
    proposedBy: json.proposed_by,
    proposedAt: json.proposed_at,
    decidedBy: json.decided_by,
    decidedAt: json.decided_at,
    decisionNote: json.decision_note,
  };
}

/** The field problems the backend names (`_FIELDS` in product_types/service.py). */
const FIELD_CODES: readonly ProductTypesErrorCode[] = [
  "invalid_code",
  "invalid_name",
  "invalid_description",
  "invalid_tax",
  "code_taken",
  "name_taken",
  "reason_required",
  "note_required",
  "nothing_changes",
  "already_retired",
  "not_retired",
  "stale",
];

export function toProductTypesError(error: unknown): ProductTypesError {
  if (error instanceof ProductTypesError) return error;
  if (!(error instanceof ApiError)) return new ProductTypesError("unavailable");
  switch (error.code) {
    case "validation_failed":
      return new ProductTypesError(FIELD_CODES.find((code) => code === error.fieldCode) ?? "invalid");
    case "conflict":
      return new ProductTypesError("conflict");
    case "not_found":
      return new ProductTypesError("not_found");
    case "forbidden":
      return new ProductTypesError("forbidden");
    case "confirm_it_is_you":
      return new ProductTypesError("not_confirmed");
    default:
      return new ProductTypesError("unavailable");
  }
}

export function createHttpProductTypesApi(http: Http): ProductTypesApi {
  const change = (id: string, action: string) => `/staff/product-type-changes/${encodeURIComponent(id)}/${action}`;

  async function call<T>(work: () => Promise<T>): Promise<T> {
    try {
      return await work();
    } catch (error) {
      throw toProductTypesError(error);
    }
  }

  const post = (path: string, body?: unknown) => call(async () => toProductTypeChange(await http.request<ProductTypeChangeOut>("POST", path, body)));

  return {
    listTypes: () =>
      call(async () => {
        const json = await http.request<ProductTypesOut>("GET", "/staff/product-types");
        return { types: json.types.map(toProductType), models: json.models.map((m) => ({ model: m.model, description: m.description })) };
      }),
    listChanges: (status) =>
      call(async () => {
        const query = status ? `?${new URLSearchParams({ status }).toString()}` : "";
        return (await http.request<ProductTypeChangeOut[]>("GET", `/staff/product-type-changes${query}`)).map(toProductTypeChange);
      }),
    proposeType: (draft) =>
      post("/staff/product-type-changes/new-type", {
        code: draft.code,
        model: draft.model,
        name: draft.name,
        description: draft.description,
        default_tax_bps: draft.defaultTaxBps,
        reason: draft.reason,
      }),
    proposeEdit: (code, terms, reason) =>
      post("/staff/product-type-changes/edit", {
        code,
        name: terms.name,
        description: terms.description,
        default_tax_bps: terms.defaultTaxBps,
        reason,
      }),
    proposeStatus: (code, to, reason) => post("/staff/product-type-changes/status", { code, to, reason }),
    approve: (id) => post(change(id, "approve")),
    sendBack: (id, note) => post(change(id, "send-back"), { note }),
    withdraw: (id) => post(change(id, "withdraw")),
  };
}

export function connectedProductTypesApi(): ProductTypesApi {
  const base = apiBaseUrl();
  return base ? createHttpProductTypesApi(createHttp(base)) : notConnectedProductTypesApi;
}
