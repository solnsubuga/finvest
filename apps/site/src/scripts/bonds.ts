/**
 * Shows the bond issues clients can buy today, read from the API when the page loads. The site
 * is built without them, because bond prices move every day: the bonds section stays hidden,
 * and the Treasury bonds category says Coming soon, until the API lists a live issue.
 *
 * Text is only ever written with textContent, from a list parseBonds has already checked.
 */

import { formatDay, formatMoney } from "../lib/format.ts";
import { parseBonds, priceLabel, type Bond, type BondList } from "../lib/bonds.ts";

const TIMEOUT_MS = 6000;
/** The category the bonds open, in src/data/categories.ts. */
const CATEGORY = "treasury-bonds";

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className: string | null, text?: string): HTMLElementTagNameMap[K] {
  const made = document.createElement(tag);
  if (className) made.className = className;
  if (text !== undefined) made.textContent = text;
  return made;
}

function fact(term: string, value: string): HTMLDivElement {
  const row = element("div", null);
  row.append(element("dt", null, term), element("dd", null, value));
  return row;
}

/** One issue's card, drawn like a product card. */
export function bondCard(bond: Bond, signupUrl: string): HTMLElement {
  const open = bond.canBuy;
  const card = element("article", "card product");
  card.dataset["bond"] = bond.code;

  const tags = element("div", "product__tags");
  tags.append(element("span", "tag", `${bond.years} year Treasury bond`));
  if (!open) tags.append(element("span", "tag tag--soon", "Not open today"));
  card.append(tags);
  card.append(element("h3", "h3", bond.name));

  const rate = element("p", "product__rate");
  rate.append(element("strong", null, `${bond.couponPercent}%`), element("span", null, "a year after tax, paid every six months"));
  card.append(rate);

  const facts = element("dl", "facts-list");
  facts.append(
    fact("Matures", formatDay(bond.maturityDate)),
    fact("Next coupon", bond.nextCouponDate ? formatDay(bond.nextCouponDate) : "At maturity"),
    fact("Today's price", bond.price === null ? "Not set yet" : `${priceLabel(bond.price)} per 100`),
    fact("Minimum", `${formatMoney(bond.minimumFaceMinor, bond.currency)} face value`),
    fact("ISIN", bond.isin),
  );
  card.append(facts);

  const action = element("div", "product__action");
  if (open) {
    const invest = element("a", "button", "Invest in this");
    invest.href = signupUrl;
    invest.append(element("span", "visually-hidden", `: ${bond.name}`));
    action.append(invest);
  } else {
    action.append(element("p", "product__soon", "Not open to new investments today."));
  }
  card.append(action);
  return card;
}

function each(selector: string, run: (element: HTMLElement) => void): void {
  document.querySelectorAll<HTMLElement>(selector).forEach(run);
}

/** Fills every `[data-bond-grid]`, and shows what goes with bonds once one can be bought. */
export function showBonds(list: BondList): void {
  const listed = list.bonds.length > 0;
  const open = list.bonds.some((bond) => bond.status === "live" && bond.canBuy);
  each("[data-bond-grid]", (grid) => {
    const signup = grid.dataset["bondGrid"];
    if (!signup) return;
    grid.replaceChildren(...list.bonds.map((bond) => bondCard(bond, signup)));
  });
  each("[data-when-bonds]", (el) => (el.hidden = !listed));
  each("[data-when-no-bonds]", (el) => (el.hidden = listed));
  each("[data-bonds-updated]", (el) => (el.textContent = formatDay(list.updatedOn)));
  // The Treasury bonds category: Open now with a link, or Coming soon.
  each(`[data-category-open="${CATEGORY}"]`, (el) => (el.hidden = !open));
  each(`[data-category-soon="${CATEGORY}"]`, (el) => (el.hidden = open));
}

export async function loadBonds(endpoint: string): Promise<BondList | null> {
  try {
    const response = await fetch(endpoint, {
      credentials: "omit",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!response.ok) return null;
    return parseBonds(await response.json());
  } catch {
    return null;
  }
}

export async function startLiveBonds(endpoint: string): Promise<void> {
  const list = await loadBonds(endpoint);
  if (list !== null) showBonds(list);
}
