/**
 * Replaces the rates built into the page with today's from the API.
 *
 * The built-in figures stay if the API is slow, unreachable or sends anything unexpected.
 * Text is only ever written with textContent, and only after parseCatalogue has checked it.
 */

import { formatDay, minimumLabel } from "../lib/format.ts";
import { parseCatalogue, type Catalogue } from "../lib/products.ts";

const TIMEOUT_MS = 6000;

/** Other scripts on the page listen for this to pick up the new rates. */
export const CATALOGUE_EVENT = "finvest:catalogue";

function each(selector: string, run: (element: HTMLElement) => void): void {
  document.querySelectorAll<HTMLElement>(selector).forEach(run);
}

export function showCatalogue(catalogue: Catalogue): void {
  for (const product of catalogue.products) {
    const code = CSS.escape(product.code);
    each(`[data-rate="${code}"]`, (el) => (el.textContent = `${product.clientRatePercent}%`));
    each(`[data-minimum="${code}"]`, (el) => (el.textContent = minimumLabel(product)));
    each(`[data-when-live="${code}"]`, (el) => (el.hidden = product.status !== "live"));
    each(`[data-when-paused="${code}"]`, (el) => (el.hidden = product.status !== "paused"));
  }
  each("[data-updated]", (el) => (el.textContent = formatDay(catalogue.updatedOn)));
  document.dispatchEvent(new CustomEvent<Catalogue>(CATALOGUE_EVENT, { detail: catalogue }));
}

export async function loadCatalogue(endpoint: string): Promise<Catalogue | null> {
  try {
    const response = await fetch(endpoint, {
      credentials: "omit",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!response.ok) return null;
    return parseCatalogue(await response.json());
  } catch {
    return null;
  }
}

export async function startLiveRates(endpoint: string): Promise<void> {
  const catalogue = await loadCatalogue(endpoint);
  if (catalogue !== null) showCatalogue(catalogue);
}
