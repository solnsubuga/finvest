/**
 * Builds the product cards from today's catalogue, so a product opened, paused or taken away
 * after the site was built shows as it is now, not as it was at build time.
 *
 * The markup is the same as src/components/ProductCard.astro. Text is only ever written with
 * textContent, from a catalogue parseCatalogue has already checked.
 */

import { interestPaidLabel, kindLabel, minimumLabel, termLabel } from "../lib/format.ts";
import type { Catalogue, Product } from "../lib/products.ts";

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className: string | null,
  text?: string,
): HTMLElementTagNameMap[K] {
  const made = document.createElement(tag);
  if (className) made.className = className;
  if (text !== undefined) made.textContent = text;
  return made;
}

function fact(term: string, value: string, attribute?: [string, string]): HTMLDivElement {
  const row = element("div", null);
  row.append(element("dt", null, term));
  const dd = element("dd", null, value);
  if (attribute) dd.setAttribute(attribute[0], attribute[1]);
  row.append(dd);
  return row;
}

/** One product's card, as ProductCard.astro draws it. */
export function productCard(product: Product, signupUrl: string): HTMLElement {
  const paused = product.status === "paused";
  const card = element("article", "card product");
  card.dataset["product"] = product.code;

  const tags = element("div", "product__tags");
  tags.append(element("span", "tag", kindLabel(product.kind, product.currency)));
  const soon = element("span", "tag tag--soon", "Coming soon");
  soon.dataset["whenPaused"] = product.code;
  soon.hidden = !paused;
  tags.append(soon);
  card.append(tags);

  card.append(element("h3", "h3", product.name));

  const rate = element("p", "product__rate");
  const figure = element("strong", null, `${product.clientRatePercent}%`);
  figure.dataset["rate"] = product.code;
  rate.append(figure, element("span", null, "a year, after tax"));
  card.append(rate);

  const facts = element("dl", "facts-list");
  facts.append(
    fact("Minimum", minimumLabel(product), ["data-minimum", product.code]),
    fact("Term", termLabel(product.termDays)),
    fact("Interest paid", interestPaidLabel(product.interestPaid)),
  );
  card.append(facts);

  const action = element("div", "product__action");
  const invest = element("a", "button", "Invest in this");
  invest.href = signupUrl;
  invest.dataset["whenLive"] = product.code;
  invest.hidden = paused;
  invest.append(element("span", "visually-hidden", `: ${product.name}`));
  const notYet = element("p", "product__soon", "Not open to new investments yet.");
  notYet.dataset["whenPaused"] = product.code;
  notYet.hidden = !paused;
  action.append(invest, notYet);
  card.append(action);
  return card;
}

/**
 * Redraws every `[data-product-grid]` with a card for each product in the catalogue, in the
 * API's order. A grid with no signup link on it is left as built.
 */
export function showProductGrids(catalogue: Catalogue): void {
  document.querySelectorAll<HTMLElement>("[data-product-grid]").forEach((grid) => {
    const signup = grid.dataset["productGrid"];
    if (!signup) return;
    grid.classList.toggle("grid--4", catalogue.products.length > 3);
    grid.classList.toggle("grid--3", catalogue.products.length <= 3);
    grid.replaceChildren(...catalogue.products.map((product) => productCard(product, signup)));
  });
}
