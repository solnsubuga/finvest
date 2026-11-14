/**
 * The statement check page (finvestug.com/check): what someone typed, and what the API's answer
 * says. The API is GET /public/statement-checks/{code}; see docs/documents.md, "Checking a
 * statement". Pure, so it is tested without a browser.
 */

import { formatDay, formatMoney } from "./format.ts";
import type { Currency } from "./products.ts";

const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const LOOKALIKES: Record<string, string> = { O: "0", I: "1", L: "1" };

/** The code as stored, or null when what was typed cannot be one. The API reads it the same way. */
export function normaliseCode(typed: string): string | null {
  if (typed.length > 40) return null;
  const code = [...typed.toUpperCase()]
    .filter((char) => !" -.\t".includes(char))
    .map((char) => LOOKALIKES[char] ?? char)
    .join("");
  if (code.length !== 12 || [...code].some((char) => !ALPHABET.includes(char))) return null;
  return code;
}

/** "K7Q2-9XMA-4TPD". */
export function showCode(code: string): string {
  return `${code.slice(0, 4)}-${code.slice(4, 8)}-${code.slice(8)}`;
}

export interface Checked {
  monthly: boolean;
  periodStart: string;
  periodEnd: string;
  issuedOn: string;
  accountEnding: string;
  closing: { minor: bigint; currency: Currency }[];
}

export type CheckAnswer =
  | { result: "genuine"; statement: Checked }
  | { result: "not_found" }
  | { result: "too_many_tries"; retryAfterSeconds: number | null };

const KAMPALA = "Africa/Kampala";

function kampalaDay(instant: string): string | null {
  const when = new Date(instant);
  if (Number.isNaN(when.getTime())) return null;
  return when.toLocaleDateString("en-CA", { timeZone: KAMPALA });
}

/** The API's answer, or null when it is not one this page understands. */
export function parseCheck(body: unknown): CheckAnswer | null {
  if (typeof body !== "object" || body === null) return null;
  const answer = body as Record<string, unknown>;
  if (answer["result"] === "not_found") return { result: "not_found" };
  if (answer["result"] === "too_many_tries") {
    const wait = answer["retry_after_seconds"];
    return { result: "too_many_tries", retryAfterSeconds: typeof wait === "number" ? wait : null };
  }
  if (answer["result"] !== "genuine") return null;
  const s = answer["statement"] as Record<string, unknown> | null | undefined;
  if (!s) return null;
  const day = /^\d{4}-\d{2}-\d{2}$/;
  const issuedOn = typeof s["issued_at"] === "string" ? kampalaDay(s["issued_at"]) : null;
  const closing = Array.isArray(s["closing"]) ? s["closing"] : null;
  if (
    (s["kind"] !== "monthly" && s["kind"] !== "requested") ||
    typeof s["period_start"] !== "string" ||
    !day.test(s["period_start"]) ||
    typeof s["period_end"] !== "string" ||
    !day.test(s["period_end"]) ||
    issuedOn === null ||
    typeof s["account_ending"] !== "string" ||
    !/^\d{4}$/.test(s["account_ending"]) ||
    closing === null ||
    closing.length === 0
  ) {
    return null;
  }
  const amounts: Checked["closing"] = [];
  for (const each of closing as unknown[]) {
    const item = each as Record<string, unknown> | null;
    if (!item || !Number.isSafeInteger(item["minor"]) || (item["currency"] !== "UGX" && item["currency"] !== "USD")) {
      return null;
    }
    amounts.push({ minor: BigInt(item["minor"] as number), currency: item["currency"] });
  }
  return {
    result: "genuine",
    statement: {
      monthly: s["kind"] === "monthly",
      periodStart: s["period_start"],
      periodEnd: s["period_end"],
      issuedOn,
      accountEnding: s["account_ending"],
      closing: amounts,
    },
  };
}

/** The lines the page shows for a genuine statement, label and value, in order. */
export function describeChecked(statement: Checked): [string, string][] {
  return [
    ["Statement", statement.monthly ? "Monthly statement" : "Statement the client asked for"],
    ["Period", `${formatDay(statement.periodStart)} to ${formatDay(statement.periodEnd)}`],
    ["Issued", formatDay(statement.issuedOn)],
    ["Account number ends", statement.accountEnding],
    ...statement.closing.map(
      (amount): [string, string] => [
        statement.closing.length > 1 ? `Value at the end, ${amount.currency}` : "Value at the end",
        formatMoney(amount.minor, amount.currency),
      ],
    ),
  ];
}
