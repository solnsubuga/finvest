// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { parseBonds, priceLabel } from "../lib/bonds.ts";
import { showBonds } from "./bonds.ts";

const SIGNUP = "https://app.finvestug.com/signup";

/** `GET /public/bonds` as the API sends it. */
function answer(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    updated_on: "2026-11-16",
    bonds: [
      {
        code: "BOND-2Y-2028",
        isin: "UG12K1502281",
        name: "2 Year Treasury Bond 2028",
        currency: "UGX",
        coupon_percent: "11.40",
        issue_date: "2026-05-16",
        maturity_date: "2028-11-16",
        years: 2,
        next_coupon_date: "2027-05-16",
        coupons_left: 4,
        minimum_face_minor: 100_000,
        face_step_minor: 100_000,
        price: 989_450,
        status: "live",
        can_buy: true,
        ...overrides,
      },
    ],
  };
}

function page(): void {
  document.body.innerHTML = `
    <section id="bonds" data-when-bonds hidden><span data-bonds-updated></span><div data-bond-grid="${SIGNUP}"></div></section>
    <p><span data-when-no-bonds>Treasury bonds are coming soon to Finvest.</span><span data-when-bonds hidden>Listed above.</span></p>
    <li id="treasury-bonds">
      <span data-category-open="treasury-bonds" hidden>Open now</span>
      <span data-category-soon="treasury-bonds">Coming soon</span>
      <a data-category-open="treasury-bonds" hidden href="#bonds">See today's bonds</a>
    </li>`;
}

const shown = (selector: string) => !(document.querySelector<HTMLElement>(selector) as HTMLElement).hidden;

describe("bonds from the API", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("draws a card for each issue with the coupon after tax and opens the category", () => {
    page();
    const list = parseBonds(answer());
    expect(list).not.toBeNull();
    showBonds(list!);
    const card = document.querySelector<HTMLElement>("[data-bond='BOND-2Y-2028']") as HTMLElement;
    expect(card.querySelector("h3")?.textContent).toBe("2 Year Treasury Bond 2028");
    expect(card.querySelector(".product__rate strong")?.textContent).toBe("11.40%");
    const facts = [...card.querySelectorAll("dl div")].map((row) => row.textContent);
    expect(facts).toEqual([
      "Matures16 November 2028",
      "Next coupon16 May 2027",
      "Today's price98.9450 per 100",
      "MinimumUGX 100,000 face value",
      "ISINUG12K1502281",
    ]);
    expect(card.querySelector<HTMLAnchorElement>("a.button")?.href).toBe(SIGNUP);
    expect(shown("#bonds")).toBe(true);
    expect(shown("[data-when-no-bonds]")).toBe(false);
    expect(document.querySelector("[data-bonds-updated]")?.textContent).toBe("16 November 2026");
    expect(shown("span[data-category-open]")).toBe(true);
    expect(shown("a[data-category-open]")).toBe(true);
    expect(shown("[data-category-soon]")).toBe(false);
    // Never the coupon before tax or the tax.
    expect(document.body.textContent?.toLowerCase()).not.toMatch(/before tax|tax rate|withholding/);
  });

  it("lists a paused issue without a way to invest, and keeps the category coming soon", () => {
    page();
    showBonds(parseBonds(answer({ status: "paused", can_buy: false }))!);
    const card = document.querySelector<HTMLElement>("[data-bond]") as HTMLElement;
    expect(card.querySelector("a.button")).toBeNull();
    expect(card.querySelector(".product__soon")?.textContent).toBe("Not open to new investments today.");
    expect(shown("[data-category-soon]")).toBe(true);
    expect(shown("span[data-category-open]")).toBe(false);
  });

  it("keeps the section hidden when there are no bonds today", () => {
    page();
    showBonds(parseBonds({ updated_on: "2026-11-16", bonds: [] })!);
    expect(shown("#bonds")).toBe(false);
    expect(shown("[data-when-no-bonds]")).toBe(true);
  });

  it("refuses an answer that is not what the site expects", () => {
    expect(parseBonds(answer({ coupon_percent: 11.4 }))).toBeNull();
    expect(parseBonds(answer({ isin: "<b>" }))).toBeNull();
    expect(parseBonds(answer({ price: 1.5 }))).toBeNull();
    expect(parseBonds({ updated_on: "today", bonds: [] })).toBeNull();
    expect(parseBonds({ updated_on: "2026-11-16" })).toBeNull();
  });

  it("writes a price per 100 exactly, to four places", () => {
    expect(priceLabel(989_450)).toBe("98.9450");
    expect(priceLabel(1_000_000)).toBe("100.0000");
    expect(priceLabel(1_025_099)).toBe("102.5099");
  });
});
