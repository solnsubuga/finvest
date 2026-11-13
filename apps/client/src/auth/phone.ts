/**
 * Ugandan mobile numbers: country code 256 followed by nine digits that start
 * with 7. We store them in E.164 form (+2567XXXXXXXX) and show the national
 * part grouped as "772 123 456".
 */

export const COUNTRY_CODE = "+256";

const NATIONAL_LENGTH = 9;

/** Strips everything but digits, then any country code or trunk zero. */
export function nationalDigits(input: string): string {
  let digits = input.replace(/\D/g, "");
  if (digits.startsWith("256") && digits.length > NATIONAL_LENGTH) {
    digits = digits.slice(3);
  }
  if (digits.startsWith("0")) {
    digits = digits.slice(1);
  }
  return digits.slice(0, NATIONAL_LENGTH);
}

export function isValidNational(digits: string): boolean {
  return /^7\d{8}$/.test(digits);
}

/** Returns the E.164 number, or null when the input is not a valid mobile number. */
export function toE164(input: string): string | null {
  const digits = nationalDigits(input);
  return isValidNational(digits) ? `${COUNTRY_CODE}${digits}` : null;
}

/** Groups the national digits in threes for display while typing. */
export function formatNational(input: string): string {
  const digits = nationalDigits(input);
  return [digits.slice(0, 3), digits.slice(3, 6), digits.slice(6, 9)]
    .filter((part) => part.length > 0)
    .join(" ");
}

/** "+256 772 ••• 456", for screens that confirm where a code was sent. */
export function maskE164(e164: string): string {
  const digits = nationalDigits(e164);
  return `${COUNTRY_CODE} ${digits.slice(0, 3)} ••• ${digits.slice(6, 9)}`;
}

/** "+256 772 123 456" */
export function formatE164(e164: string): string {
  return `${COUNTRY_CODE} ${formatNational(e164)}`;
}
