/**
 * The "what you could earn" calculator.
 *
 * It answers at once with an estimate from the rate on the page, then asks the API for the
 * exact figure and shows that instead when it arrives. If the API cannot be reached the
 * estimate stays, labelled as one. See lib/estimate.ts for why the two can differ.
 */

import { estimate } from "../lib/estimate.ts";
import { addDays, formatDay, formatMoney, formatWhole, parseWhole, todayInKampala } from "../lib/format.ts";
import { parseQuote, type Catalogue, type Product, type Quote } from "../lib/products.ts";
import { CATALOGUE_EVENT } from "./live.ts";

const QUOTE_DELAY_MS = 350;
const QUOTE_TIMEOUT_MS = 6000;

async function fetchQuote(url: string, product: string, amount: bigint): Promise<Quote | "off" | null> {
  try {
    const query = new URLSearchParams({ product, amount_minor: amount.toString() });
    const response = await fetch(`${url}?${query}`, {
      credentials: "omit",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(QUOTE_TIMEOUT_MS),
    });
    // The API is there but has no quote route, or is failing: stop asking on this page view.
    if (response.status === 404 || response.status >= 500) return "off";
    if (!response.ok) return null;
    return parseQuote(await response.json(), product, amount);
  } catch {
    return "off";
  }
}

function start(form: HTMLFormElement): void {
  const input = form.querySelector<HTMLInputElement>('input[name="amount"]');
  const error = form.querySelector<HTMLElement>("[data-calc-error]");
  const when = form.querySelector<HTMLElement>("[data-calc-when]");
  const total = form.querySelector<HTMLElement>("[data-calc-total]");
  const interest = form.querySelector<HTMLElement>("[data-calc-interest]");
  const note = form.querySelector<HTMLElement>("[data-calc-note]");
  const quoteUrl = form.dataset["quoteUrl"];
  if (!input || !error || !when || !total || !interest || !note) return;

  let products: Product[] = [];
  try {
    products = JSON.parse(form.dataset["products"] ?? "[]") as Product[];
  } catch {
    return;
  }

  let quotes = quoteUrl !== undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let asked = 0;

  const chosen = (): Product | undefined => {
    const code = new FormData(form).get("product");
    return products.find((p) => p.code === code);
  };

  const refuse = (message: string): void => {
    error.textContent = message;
    error.hidden = false;
    input.setAttribute("aria-invalid", "true");
    when.textContent = "You get back";
    total.textContent = "UGX —";
    interest.hidden = true;
  };

  const update = (): void => {
    clearTimeout(timer);
    asked += 1;
    const product = chosen();
    if (!product || product.termDays === null) return;
    const days = product.termDays;
    const rate = product.clientRatePercent;

    const amount = parseWhole(input.value);
    if (amount === null) {
      refuse("Enter an amount in whole shillings, for example 1,000,000.");
      return;
    }
    if (amount < BigInt(product.minimumMinor)) {
      refuse(`The smallest amount for ${days} days is ${formatMoney(product.minimumMinor, "UGX")}.`);
      return;
    }
    const guess = estimate(amount, rate, days);
    if (guess === null) return;

    error.hidden = true;
    input.removeAttribute("aria-invalid");
    interest.hidden = false;
    when.textContent = `On ${formatDay(addDays(todayInKampala(), days))} you get back about`;
    total.textContent = formatMoney(guess.total, "UGX");
    interest.textContent = `About ${formatMoney(guess.interest, "UGX")} interest, after tax`;
    note.textContent =
      `An estimate at today's rate of ${rate}% a year after tax. The app shows the exact ` +
      "amount before you pay. The rate is fixed on the day your investment starts.";

    if (!quotes || quoteUrl === undefined) return;
    const request = asked;
    timer = setTimeout(() => {
      void fetchQuote(quoteUrl, product.code, amount).then((quote) => {
        if (quote === "off") quotes = false;
        // Only show an answer to the question still on the screen.
        if (quote === null || quote === "off" || request !== asked) return;
        when.textContent = `You get back on ${formatDay(quote.maturesOn)}`;
        total.textContent = formatMoney(quote.totalMinor, quote.currency);
        interest.textContent = `${formatMoney(quote.interestMinor, quote.currency)} interest, after tax`;
        note.textContent =
          `At today's rate of ${rate}% a year after tax. The rate is fixed on the day your ` +
          "investment starts.";
      });
    }, QUOTE_DELAY_MS);
  };

  form.addEventListener("submit", (event) => event.preventDefault());
  form.addEventListener("input", update);
  input.addEventListener("blur", () => {
    const amount = parseWhole(input.value);
    if (amount !== null) input.value = formatWhole(amount);
  });

  // Today's rates arrived from the API: use them for the products this calculator offers.
  document.addEventListener(CATALOGUE_EVENT, (event) => {
    const live = (event as CustomEvent<Catalogue>).detail.products;
    products = products.map((mine) => {
      const theirs = live.find((p) => p.code === mine.code);
      return theirs && theirs.termDays !== null && theirs.currency === "UGX" ? theirs : mine;
    });
    update();
  });

  update();
}

export function startCalculators(): void {
  document.querySelectorAll<HTMLFormElement>("form[data-calculator]").forEach(start);
}
