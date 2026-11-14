import { DetailField, IdentityDetails } from "./types";

/**
 * The details form: what the client types, checked the way the API checks it
 * (backend/app/modules/kyc/domain/checks.py, detail_problems). The API checks again.
 */

export const MINIMUM_AGE = 18;
const NAME = /^[\p{L}]+(?:[ '\-.][\p{L}]+)*\.?$/u;
const DOCUMENT_NUMBER = /^[A-Z0-9]{5,32}$/;
const TIN = /^[0-9]{10}$/;

/** What the client typed. Dates as "DD/MM/YYYY", as printed on Ugandan documents. */
export type DetailsForm = {
  surname: string;
  givenNames: string;
  documentNumber: string;
  dateOfBirth: string;
  expiresOn: string;
  tin: string;
};

export type DetailErrors = Partial<Record<DetailField, string>>;

const problemCopy: Record<string, string> = {
  invalid_name: "Use letters only, as printed on your ID.",
  invalid_document_number: "Use the 5 to 32 letters and digits printed on your ID.",
  in_the_future: "That date is in the future.",
  under_age: `You must be ${MINIMUM_AGE} or older to open a Finvest account.`,
  document_expired: "This ID has expired. Use one that is still valid.",
  invalid_tin: "A TIN is 10 digits. Leave it empty if you do not have one.",
  invalid_date: "Enter the date as day, month and year, like 22/06/1994.",
  missing: "Fill this in.",
};

export function describeDetailProblem(code: string): string {
  return problemCopy[code] ?? "This is not right. Check it against your ID.";
}

function collapse(value: string): string {
  return value.trim().split(/\s+/).filter(Boolean).join(" ");
}

/** "22/06/1994", "22-06-1994" or "22 06 1994" as "1994-06-22". Null when it is not a real date. */
export function parseDay(text: string): string | null {
  const match = /^(\d{1,2})[/\-. ](\d{1,2})[/\-. ](\d{4})$/.exec(text.trim());
  if (!match) return null;
  const [day, month, year] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    return null;
  }
  return date.toISOString().slice(0, 10);
}

/** "1994-06-22" as "22/06/1994", to fill the form again. */
export function showDay(iso: string): string {
  const [year, month, day] = iso.split("-");
  return `${day}/${month}/${year}`;
}

function yearsBetween(earlier: string, later: string): number {
  const [ey, em, ed] = earlier.split("-").map(Number) as [number, number, number];
  const [ly, lm, ld] = later.split("-").map(Number) as [number, number, number];
  const beforeBirthday = lm < em || (lm === em && ld < ed);
  return ly - ey - (beforeBirthday ? 1 : 0);
}

export function emptyForm(details: IdentityDetails | null): DetailsForm {
  if (!details) {
    return { surname: "", givenNames: "", documentNumber: "", dateOfBirth: "", expiresOn: "", tin: "" };
  }
  return {
    surname: details.surname,
    givenNames: details.givenNames,
    documentNumber: details.documentNumber,
    dateOfBirth: showDay(details.dateOfBirth),
    expiresOn: showDay(details.expiresOn),
    tin: details.tin ?? "",
  };
}

/**
 * The details to send, or what is wrong with them. `today` is the date in
 * Kampala, "YYYY-MM-DD".
 */
export function readForm(
  form: DetailsForm,
  today: string,
): { ok: true; details: IdentityDetails } | { ok: false; errors: DetailErrors } {
  const errors: DetailErrors = {};
  const surname = collapse(form.surname);
  const givenNames = collapse(form.givenNames);
  const documentNumber = form.documentNumber.replace(/[\s-]/g, "").toUpperCase();
  const tin = form.tin.replace(/\s/g, "");

  for (const [field, value] of [
    ["surname", surname],
    ["givenNames", givenNames],
  ] as const) {
    if (!value) errors[field] = problemCopy.missing;
    else if (value.length > 100 || !NAME.test(value)) errors[field] = problemCopy.invalid_name;
  }
  if (!documentNumber) errors.documentNumber = problemCopy.missing;
  else if (!DOCUMENT_NUMBER.test(documentNumber)) errors.documentNumber = problemCopy.invalid_document_number;

  const dateOfBirth = parseDay(form.dateOfBirth);
  if (!form.dateOfBirth.trim()) errors.dateOfBirth = problemCopy.missing;
  else if (!dateOfBirth) errors.dateOfBirth = problemCopy.invalid_date;
  else if (dateOfBirth > today) errors.dateOfBirth = problemCopy.in_the_future;
  else if (yearsBetween(dateOfBirth, today) < MINIMUM_AGE) errors.dateOfBirth = problemCopy.under_age;

  const expiresOn = parseDay(form.expiresOn);
  if (!form.expiresOn.trim()) errors.expiresOn = problemCopy.missing;
  else if (!expiresOn) errors.expiresOn = problemCopy.invalid_date;
  else if (expiresOn < today) errors.expiresOn = problemCopy.document_expired;

  if (tin && !TIN.test(tin)) errors.tin = problemCopy.invalid_tin;

  if (Object.keys(errors).length > 0 || !dateOfBirth || !expiresOn) return { ok: false, errors };
  return { ok: true, details: { surname, givenNames, documentNumber, dateOfBirth, expiresOn, tin: tin || null } };
}
