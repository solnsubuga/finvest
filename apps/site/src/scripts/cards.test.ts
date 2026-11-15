// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { fallback } from "../data/fallback.ts";
import type { Catalogue } from "../lib/products.ts";
import { calculatorChoices } from "./calculator.ts";
import { showCatalogue } from "./live.ts";

// jsdom has no CSS.escape. Product codes are letters, digits and hyphens, which need none.
globalThis.CSS ??= { escape: (value: string) => value.replace(/[^\w-]/g, (c) => `\\${c}`) } as unknown as typeof CSS;

const SIGNUP = "https://app.finvestug.com/signup";

const today: Catalogue = {
  updatedOn: "2026-11-15",
  products: [
    { ...(fallback.products[0] as Catalogue["products"][number]), clientRatePercent: "9.00" },
    {
      code: "TREA-364",
      name: "364 Day Treasury Bill",
      kind: "treasury_bill",
      currency: "UGX",
      termDays: 364,
      minimumMinor: 500_000,
      interestPaid: "at_maturity",
      clientRatePercent: "11.60",
      status: "live",
    },
  ],
};

function page(): HTMLElement {
  document.body.innerHTML = `<div class="grid grid--3" data-product-grid="${SIGNUP}">
    ${fallback.products.map((p) => `<article data-product="${p.code}"><strong data-rate="${p.code}">${p.clientRatePercent}%</strong></article>`).join("")}
  </div><p><span data-rate="TREA-364"></span></p>`;
  return document.querySelector<HTMLElement>("[data-product-grid]") as HTMLElement;
}

describe("product cards from today's catalogue", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("adds a product created since the build and drops one no longer listed", () => {
    const grid = page();
    showCatalogue(today);
    const cards = [...grid.querySelectorAll<HTMLElement>("article")];
    expect(cards.map((card) => card.dataset["product"])).toEqual(["TREA-91", "TREA-364"]);
    const added = cards[1] as HTMLElement;
    expect(added.querySelector("h3")?.textContent).toBe("364 Day Treasury Bill");
    expect(added.querySelector("[data-rate]")?.textContent).toBe("11.60%");
    expect(added.querySelector("[data-minimum]")?.textContent).toBe("UGX 500,000");
    expect(added.querySelector<HTMLAnchorElement>("a.button")?.href).toBe(SIGNUP);
    expect(cards[0]?.querySelector("[data-rate]")?.textContent).toBe("9.00%");
    // A rate shown elsewhere on the page follows too.
    expect(document.querySelector("p [data-rate='TREA-364']")?.textContent).toBe("11.60%");
  });

  it("shows a paused product as coming soon, with no way to invest", () => {
    const grid = page();
    showCatalogue({ ...today, products: [{ ...(today.products[1] as Catalogue["products"][number]), status: "paused" }] });
    const card = grid.querySelector("article") as HTMLElement;
    expect(card.querySelector<HTMLElement>(".tag--soon")?.hidden).toBe(false);
    expect(card.querySelector<HTMLElement>("a.button")?.hidden).toBe(true);
  });

  it("writes names as text, never as markup", () => {
    const grid = page();
    showCatalogue({ ...today, products: [{ ...(today.products[1] as Catalogue["products"][number]), name: "<img src=x onerror=alert(1)>" }] });
    expect(grid.querySelector("img")).toBeNull();
    expect(grid.querySelector("h3")?.textContent).toBe("<img src=x onerror=alert(1)>");
  });

  it("sizes the grid for the number of cards", () => {
    const grid = page();
    showCatalogue({ ...today, products: [...fallback.products, today.products[1] as Catalogue["products"][number]] });
    expect(grid.classList.contains("grid--4")).toBe(true);
    showCatalogue(today);
    expect(grid.classList.contains("grid--3")).toBe(true);
    expect(grid.classList.contains("grid--4")).toBe(false);
  });
});

describe("calculator choices", () => {
  it("offers every open shilling term, shortest first, including a new one", () => {
    const codes = calculatorChoices([...today.products].reverse()).map((p) => p.code);
    expect(codes).toEqual(["TREA-91", "TREA-364"]);
  });

  it("leaves out open terms, dollars and paused products", () => {
    expect(calculatorChoices(fallback.products.filter((p) => p.termDays === null || p.currency === "USD"))).toEqual([]);
  });
});
