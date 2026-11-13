/**
 * Facts the public site must carry before it can be opened to search engines. An investment
 * site that names no company, regulator or licence must not launch, even with every claim
 * signed off. `pnpm placeholders --strict` fails while any of these is missing.
 */

export const requiredFacts = ["registeredName", "regulator", "licenceNumber"] as const;

export type RequiredFact = (typeof requiredFacts)[number];

export function missingFacts(facts: Record<RequiredFact, string | null>): RequiredFact[] {
  return requiredFacts.filter((key) => {
    const value = facts[key];
    return value === null || value.trim() === "";
  });
}
