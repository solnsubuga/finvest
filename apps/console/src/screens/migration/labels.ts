import type { Blocker, Currency, Difference, Figure, Problem, SheetName, TotalLine } from "./api.ts";

export const SHEET_LABELS: Record<SheetName, string> = { clients: "Clients", investments: "Investments" };

export const FIGURE_LABELS: Record<Figure, string> = {
  clients: "Number of clients",
  verified_identities: "Verified identities",
  running_investments: "Running investments",
  pending_orders: "Pending orders",
  invested: "Money invested",
  interest_before_tax: "Interest earned so far, before tax",
};

/** What each problem code from the backend means, in words staff can act on. */
const PROBLEM_LABELS: Record<string, string> = {
  column_missing: "This column is missing from the sheet",
  column_unknown: "This column is not part of the format",
  column_repeated: "A column appears twice",
  too_many_rows: "The sheet has more than 100,000 rows",
  not_a_csv_file: "The file is not a CSV file",
  row_wrong_length: "The row has too many or too few cells",
  missing: "This cell is empty and is needed",
  missing_for_verified_identity: "Needed because the identity is marked as verified",
  too_long: "The text is too long",
  not_expected: "This cell must be empty for this row",
  reference_invalid: "A reference uses only letters, digits, dots, slashes and hyphens",
  value_not_recognised: "This is not one of the accepted values",
  yes_or_no_expected: "Write yes or no",
  date_invalid: "Write the date as YYYY-MM-DD",
  phone_invalid: "The phone number cannot be read",
  email_invalid: "The email address cannot be read",
  amount_invalid: "The amount cannot be read",
  amount_not_positive: "The amount must be more than zero",
  rate_invalid: "Write the rate as a percentage with at most two decimal places",
  rate_out_of_range: "The rate must be above 0% and at most 50%",
  in_the_future: "The date is in the future",
  id_number_invalid: "The ID number cannot be read",
  product_unknown: "There is no product with this code",
  starts_after_as_of: "The start date is after the “as of” date",
  maturity_differs: "The maturity date is not the start date plus the product's term",
  tax_exceeds_interest: "The tax is more than the interest",
  duplicate_reference: "This reference is used on more than one row",
  duplicate_phone: "Another client has the same phone number",
  duplicate_id: "Another client has the same identity document",
  duplicate_account_number: "Another client has the same old account number",
  client_unknown: "This client is not in the clients sheet",
  phone_missing: "No phone number, so the client cannot be invited yet",
  under_age: "The client is under 18",
  id_not_verified: "Identity was not verified in Finvest 1.0. The client redoes the ID check",
  id_expired: "The identity document has expired. The client redoes the ID check",
  id_expiry_unknown: "No expiry date on file for the identity document",
  below_minimum: "The amount is below today's minimum for the product",
  past_maturity: "Past its maturity date and not yet paid out",
  duplicate_email: "Another client has the same email address",
  possible_duplicate_person: "Another client has the same name and date of birth",
  client_without_investments: "This client has no investments in the file",
};

export function problemLabel(problem: Problem): string {
  return PROBLEM_LABELS[problem.code] ?? `Problem “${problem.code}”`;
}

export const BLOCKER_LABELS: Record<Blocker, string> = {
  no_trial: "Run a trial import",
  import_has_errors: "Fix every error in the spreadsheet",
  totals_not_entered: "Finance enters the Finvest 1.0 totals",
  totals_not_confirmed: "A second person confirms the Finvest 1.0 totals",
  difference_not_signed_off: "Finance signs off every difference with a reason",
};

const MINOR_DIGITS: Record<Currency, number> = { UGX: 0, USD: 2 };

function group(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** A whole-number string in the smallest unit, as "1,500,000" or "1,500.50". No floats. */
export function formatAmount(minor: string, currency: Currency | null): string {
  const negative = minor.startsWith("-");
  const digits = negative ? minor.slice(1) : minor;
  const places = currency ? MINOR_DIGITS[currency] : 0;
  const padded = digits.padStart(places + 1, "0");
  const whole = group(padded.slice(0, padded.length - places));
  const fraction = places ? `.${padded.slice(-places)}` : "";
  return `${negative ? "-" : ""}${whole}${fraction}`;
}

export function formatWithCurrency(minor: string, currency: Currency): string {
  return `${currency === "USD" ? "$" : "UGX "}${formatAmount(minor, currency)}`;
}

/** Old minus new, as a whole-number string. */
export function difference(old: string, next: string): string {
  return (BigInt(old) - BigInt(next)).toString();
}

export type LineResult = "agrees" | "look_into" | "waiting" | "signed_off" | "not_entered";

export function lineResult(line: TotalLine): LineResult {
  if (line.old === null) return "not_entered";
  // Compared as numbers, so "0100" and "100" agree. Never as text, never as floats.
  if (BigInt(line.old) === BigInt(line.new)) return "agrees";
  if (!line.signedOff || line.signedOff.state === "rejected") return "look_into";
  return line.signedOff.state === "approved" ? "signed_off" : "waiting";
}

export const RESULT_LABELS: Record<LineResult, string> = {
  agrees: "Agrees",
  look_into: "Look into",
  waiting: "Waiting for approval",
  signed_off: "Signed off",
  not_entered: "Not entered",
};

/** The largest whole number a browser holds exactly, which is also the backend's limit. */
const MAX_WHOLE = 2n ** 53n - 1n;

/** A count typed by a person, as "1,912" or "1912". Null when it is not a whole number. */
export function parseCount(text: string): string | null {
  const cleaned = text.trim().replace(/,/g, "");
  if (!/^\d{1,16}$/.test(cleaned)) return null;
  const value = BigInt(cleaned);
  return value <= MAX_WHOLE ? value.toString() : null;
}

/**
 * An amount typed by a person, in the smallest unit: "1,500.50" dollars is
 * "150050" cents. Shillings have no fraction. Worked out on the digits, so no
 * floating point number is ever made. Empty means none of that currency.
 */
export function parseAmount(text: string, currency: Currency): string | null {
  const cleaned = text.trim().replace(/,/g, "");
  if (cleaned === "") return "0";
  const places = MINOR_DIGITS[currency];
  const pattern = places ? /^(\d{1,16})(?:\.(\d{1,2}))?$/ : /^(\d{1,16})$/;
  const match = pattern.exec(cleaned);
  if (!match) return null;
  const value = BigInt(match[1]!) * 10n ** BigInt(places) + BigInt((match[2] ?? "").padEnd(places, "0") || "0");
  return value <= MAX_WHOLE ? value.toString() : null;
}

/** Bytes as "40 MB", for the size limit on a sheet. */
export function formatMegabytes(bytes: number): string {
  return `${Math.floor(bytes / 1_000_000)} MB`;
}

export function lineLabel(line: TotalLine): string {
  return line.currency ? `${FIGURE_LABELS[line.figure]}, ${line.currency}` : FIGURE_LABELS[line.figure];
}

/** Basis points as a percentage: 1500 is "15%", 1250 is "12.5%". */
export function formatRate(bps: string): string {
  const value = BigInt(bps);
  const fraction = (value % 100n).toString().padStart(2, "0").replace(/0+$/, "");
  return `${value / 100n}${fraction ? `.${fraction}` : ""}%`;
}

export function differenceSummary(item: Difference): string {
  if (item.kind === "tax_rate") {
    return `Tax of ${formatRate(item.old)} stated, the product withholds ${formatRate(item.new)}`;
  }
  const gap = difference(item.old, item.new);
  const more = !gap.startsWith("-");
  const size = more ? gap : gap.slice(1);
  const amount = item.currency ? formatWithCurrency(size, item.currency) : `${formatAmount(size, null)} in the smallest unit`;
  return gap === "0" ? "The tax held differs" : `Finvest 1.0 shows ${amount} ${more ? "more" : "less"} interest`;
}

const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const MOMENT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Africa/Kampala",
});

export function formatDay(day: string): string {
  return DAY.format(new Date(`${day}T00:00:00Z`));
}

export function formatMoment(instant: string): string {
  return MOMENT.format(new Date(instant));
}
