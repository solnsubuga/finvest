/**
 * Lines the pages show only once Finvest has supplied the fact behind them
 * (src/data/company.ts). Each helper returns nothing for a missing or malformed fact, so a
 * page never shows a button that goes nowhere.
 */

export interface StoreLink {
  label: "App Store" | "Google Play";
  href: string;
}

const stores = [
  { key: "appStoreUrl", label: "App Store", host: "apps.apple.com" },
  { key: "playStoreUrl", label: "Google Play", host: "play.google.com" },
] as const;

/** The store buttons for the footer: only https links on the store's own address. */
export function storeLinks(facts: { appStoreUrl: string | null; playStoreUrl: string | null }): StoreLink[] {
  return stores.flatMap(({ key, label, host }) => {
    const value = facts[key]?.trim();
    if (!value) return [];
    try {
      const url = new URL(value);
      return url.protocol === "https:" && url.host === host && url.pathname.length > 1
        ? [{ label, href: url.toString() }]
        : [];
    } catch {
      return [];
    }
  });
}

/** The footer's Careers link, as a mailto, when the address is one of Finvest's own. */
export function careersLink(email: string | null): string | null {
  const value = email?.trim().toLowerCase();
  return value && /^[a-z]+@finvestug\.com$/.test(value) ? `mailto:${value}` : null;
}

/** "Amara Nakato" becomes "AN": shown in place of a photo on the team cards. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const first = words[0]?.[0] ?? "";
  const last = words.length > 1 ? (words[words.length - 1]?.[0] ?? "") : "";
  return `${first}${last}`.toUpperCase();
}
