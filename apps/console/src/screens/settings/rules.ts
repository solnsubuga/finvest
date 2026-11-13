import type { Field, Problem, ProblemCode, WordingText } from "./api.ts";

/**
 * The rules an edited wording must pass, checked as the person types so they
 * see a problem before sending. The same rules as `check` in the backend's
 * `domain/wording.py`, which decides: anything it refuses comes back as
 * problems and is shown the same way.
 */

export const LIMITS: Record<Field, number> = { title: 60, body: 300, sms: 300 };
export const OWN_SITE = "finvestug.com";

const PLACEHOLDER = /\{([a-z_]+)\}/g;
// Anything a phone or a mail client would turn into a link, as the backend reads it.
const LINK = /(?:https?:\/\/|www\.)[^\s]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b(?:[/?#][^\s]*)?/gi;

function host(link: string): string {
  const rest = link.replace(/^https?:\/\//i, "");
  const authority = rest.split(/[/?#]/, 1)[0] ?? "";
  const name = (authority.split("@").pop() ?? "").split(":", 1)[0] ?? "";
  return name.toLowerCase().replace(/\.+$/, "").replace(/^www\./, "");
}

/** Every link in `text` that goes anywhere but Finvest's own site. */
export function outsideLinks(text: string): string[] {
  return [...text.matchAll(LINK)].map((match) => match[0]).filter((link) => {
    const name = host(link);
    return name !== OWN_SITE && !name.endsWith(`.${OWN_SITE}`);
  });
}

function fieldProblems(field: Field, value: string, variables: readonly string[]): ProblemCode[] {
  if (value.trim().length === 0) return ["required"];
  const problems: ProblemCode[] = [];
  if (value.length > LIMITS[field]) problems.push("too_long");
  // eslint-disable-next-line no-control-regex -- control characters are exactly what is refused
  if (value !== value.trim() || /[\u0000-\u001f]/.test(value)) problems.push("invalid_characters");
  const names = [...value.matchAll(PLACEHOLDER)].map((match) => match[1] as string);
  if (names.some((name) => !variables.includes(name))) problems.push("unknown_placeholder");
  if (new Set(names).size !== names.length) problems.push("repeated_placeholder");
  if (/[{}]/.test(value.replace(PLACEHOLDER, ""))) problems.push("stray_brace");
  if (outsideLinks(value).length > 0) problems.push("outside_link");
  if (value.includes("%")) problems.push("no_rates");
  return problems;
}

export function checkWording(wording: WordingText, variables: readonly string[]): Problem[] {
  const problems: Problem[] = (["title", "body", "sms"] as const).flatMap((field) =>
    fieldProblems(field, wording[field], variables).map((code) => ({ field, code })),
  );
  // Anything outside the basic alphabet makes a text message cost several.
  // eslint-disable-next-line no-control-regex -- the range is the basic alphabet
  if (problems.length === 0 && !/^[\u0000-\u007f]*$/.test(wording.sms)) problems.push({ field: "sms", code: "invalid_characters" });
  return problems;
}

export function problemText(code: ProblemCode, field: Field, variables: readonly string[]): string {
  switch (code) {
    case "required":
      return "Write something here.";
    case "too_long":
      return `Keep it to ${LIMITS[field]} characters.`;
    case "invalid_characters":
      return field === "sms"
        ? "Use plain letters, digits and punctuation, with no line breaks, so it goes as one text."
        : "Remove the line breaks and the spaces at the start or end.";
    case "unknown_placeholder":
      return variables.length === 0
        ? "This message has no values to fill in. Remove the braces."
        : `Use only ${variables.map((name) => `{${name}}`).join(", ")}.`;
    case "repeated_placeholder":
      return "Use each value once.";
    case "stray_brace":
      return "A brace must be part of a value, like {amount}.";
    case "outside_link":
      return `Link only to ${OWN_SITE}.`;
    case "no_rates":
      return "Remove the percent sign. Clients see net amounts, never a rate.";
    case "not_accepted":
      return "Finvest did not accept this. Change it and try again.";
  }
}

const SAMPLES: Record<string, string> = {
  amount: "UGX 1,000,000",
  method: "MTN Mobile Money",
  destination: "MTN Mobile Money",
  product: "91 Day Treasury Bill",
  date: "20 Nov 2026",
  count: "3",
  period: "October",
  device: "a new phone",
  change: "A passkey was added to your account",
  reference: "C-2611",
};

/** The words with the backend's sample values, for a preview while editing. */
export function fillSample(text: string): string {
  return text.replace(PLACEHOLDER, (whole, name: string) => SAMPLES[name] ?? whole);
}
