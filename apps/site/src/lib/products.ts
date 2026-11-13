/**
 * The product catalogue as the website shows it, and the check on what the API sends.
 *
 * Only figures a client may see live here: the yearly rate after tax, never the rate before
 * tax or the tax rate (docs/rules.md).
 */

export type Currency = "UGX" | "USD";
export type ProductKind = "treasury_bill" | "fixed_income";
export type InterestPaid = "at_maturity" | "monthly";
/** Live takes new investments. Paused is listed as coming soon and cannot be invested in. */
export type ProductStatus = "live" | "paused";

export interface Product {
  code: string;
  name: string;
  kind: ProductKind;
  currency: Currency;
  /** Days from start to maturity. `null` is an open term. */
  termDays: number | null;
  /** The smallest investment, in the smallest unit: shillings for UGX, cents for USD. */
  minimumMinor: number;
  interestPaid: InterestPaid;
  /** Yearly rate after tax, two decimal places, as a label: "10.63". */
  clientRatePercent: string;
  status: ProductStatus;
}

export interface Catalogue {
  /** The day the rates were last changed, as YYYY-MM-DD. */
  updatedOn: string;
  products: Product[];
}

const CODE = /^[A-Z0-9-]{1,20}$/;
const RATE = /^\d{1,2}\.\d{2}$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const MAX_PRODUCTS = 20;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function wholeNumber(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max;
}

function parseProduct(value: unknown): Product | null {
  if (!isRecord(value)) return null;
  const { code, name, kind, currency, term_days, minimum_minor, interest_paid } = value;
  const rate = value["client_rate_percent"];
  const status = value["status"];
  if (typeof code !== "string" || !CODE.test(code)) return null;
  if (typeof name !== "string" || name.trim() === "" || name.length > 80) return null;
  if (kind !== "treasury_bill" && kind !== "fixed_income") return null;
  if (currency !== "UGX" && currency !== "USD") return null;
  if (term_days !== null && !wholeNumber(term_days, 1, 3660)) return null;
  if (!wholeNumber(minimum_minor, 1, Number.MAX_SAFE_INTEGER)) return null;
  if (interest_paid !== "at_maturity" && interest_paid !== "monthly") return null;
  if (typeof rate !== "string" || !RATE.test(rate)) return null;
  if (status !== "live" && status !== "paused") return null;
  return {
    code,
    name: name.trim(),
    kind,
    currency,
    termDays: term_days,
    minimumMinor: minimum_minor,
    interestPaid: interest_paid,
    clientRatePercent: rate,
    status,
  };
}

/**
 * Reads the API's answer. Returns `null` unless every part of it is what the site expects, so
 * a half-right answer never replaces the figures already on the page.
 */
export function parseCatalogue(value: unknown): Catalogue | null {
  if (!isRecord(value)) return null;
  const { updated_on, products } = value;
  if (typeof updated_on !== "string" || !DAY.test(updated_on)) return null;
  if (!Array.isArray(products) || products.length === 0 || products.length > MAX_PRODUCTS) {
    return null;
  }
  const parsed: Product[] = [];
  const seen = new Set<string>();
  for (const item of products) {
    const product = parseProduct(item);
    if (product === null || seen.has(product.code)) return null;
    seen.add(product.code);
    parsed.push(product);
  }
  return { updatedOn: updated_on, products: parsed };
}

/** An exact figure from the API for one amount in one product. */
export interface Quote {
  product: string;
  amountMinor: bigint;
  currency: Currency;
  maturesOn: string;
  interestMinor: bigint;
  totalMinor: bigint;
}

function amount(value: unknown): bigint | null {
  return wholeNumber(value, 0, Number.MAX_SAFE_INTEGER) ? BigInt(value) : null;
}

/**
 * Reads a quote. Returns `null` unless it is for the product and amount that were asked for
 * and its figures add up, so a late or wrong answer is never shown.
 */
export function parseQuote(value: unknown, product: string, amountMinor: bigint): Quote | null {
  if (!isRecord(value)) return null;
  const { currency, matures_on } = value;
  const asked = amount(value["amount_minor"]);
  const interest = amount(value["interest_minor"]);
  const total = amount(value["total_minor"]);
  if (value["product"] !== product || asked !== amountMinor) return null;
  if (currency !== "UGX" && currency !== "USD") return null;
  if (typeof matures_on !== "string" || !DAY.test(matures_on)) return null;
  if (interest === null || total === null || total !== amountMinor + interest) return null;
  return {
    product,
    amountMinor,
    currency,
    maturesOn: matures_on,
    interestMinor: interest,
    totalMinor: total,
  };
}
