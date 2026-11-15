/**
 * How a name from an identity document is shown: proper case, given names first.
 *
 * Documents print names in capitals ("NAKATO", "AMARA"). The check keeps them as printed, and
 * the API sends the session's name already shown this way (backend kyc/domain/names.py). This is
 * the same rule, for a name the app reads off the check itself.
 *
 * Only a word written all in capitals or all in small letters is re-cased. A word that mixes the
 * two ("McDonald") was written that way on purpose and is kept.
 */

function word(text: string): string {
  if (text !== text.toUpperCase() && text !== text.toLowerCase()) return text;
  return text
    .split(/([-'])/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join("");
}

/** "  amara   JOY " is "Amara Joy". */
export function properCase(name: string): string {
  return name.split(/\s+/).filter(Boolean).map(word).join(" ");
}

/** The first given name, to greet someone by: "AMARA JOY" is "Amara". */
export function firstGivenName(givenNames: string): string | null {
  return properCase(givenNames).split(" ")[0] || null;
}

function nameWords(name: string): string[] {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .split(/[^a-z']+/)
    .filter(Boolean)
    .sort();
}

/**
 * True when two names hold the same words, whatever their order, case or spacing: "NAKATO Amara"
 * is the same as "Amara Nakato". Only a hint for the client; staff compare the proof itself.
 */
export function sameName(a: string, b: string): boolean {
  const left = nameWords(a);
  const right = nameWords(b);
  return left.length > 0 && left.length === right.length && left.every((part, index) => part === right[index]);
}
