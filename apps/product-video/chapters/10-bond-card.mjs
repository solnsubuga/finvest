import { ensureClientSignedIn } from "../lib/client.mjs";
import { BOND } from "./06-bond.mjs";

const tid = (page, id) => page.getByTestId(id).locator("visible=true");

export default {
  id: "10",
  title: "A bond, paid by card",
  subtitle: "Part of a Treasury bond, paid on Pesapal's secure page.",
  steps: [
    { caption: "For the years after, Esther looks at the bond", does: "Invest tab, Treasury bonds, 5 Year Treasury Bond 2031" },
    { caption: "Its coupon, dates and price are all on one screen", does: "the bond's facts (zoom)" },
    { caption: "She picks a face value and sees what it costs today", does: "face value 1,000,000: price, interest since the last coupon, what she pays (zoom)" },
    { caption: "This time she pays by card, and sees the charge first", does: "Review, Confirm and pay, Card: the card charge (zoom)" },
    { caption: "The card is typed on Pesapal's page, never in Finvest", does: "Pay by card, Finish on Pesapal's secure page, Open the secure page" },
    { caption: "Confirmed, with the card it came from", does: "the sandbox card payment completes; the receipt (zoom)" },
    { caption: "Her bond waits for its first coupon in February", does: "Done, Portfolio, Bonds: the holding and its coupons (zoom)" },
  ],
  takes: [{ name: "buy", who: "esther-phone", layout: "phone" }],
  async buy(t, page) {
    await ensureClientSignedIn(page, "esther", "/invest");
    await page.getByText(BOND.name).first().waitFor();
    await t.start();
    await t.step("For the years after, Esther looks at the bond", { hold: 600 });
    await t.scroll(500, { ms: 1000 });
    await t.click(page.getByText(BOND.name).first());
    await tid(page, "bond-facts").waitFor();
    await t.read(1600);
    await t.step("Its coupon, dates and price are all on one screen", { hold: 300 });
    await t.zoom(tid(page, "bond-facts"), { hold: 2600, scale: 1.15 });
    await t.step("She picks a face value and sees what it costs today", { hold: 300 });
    await t.type(tid(page, "face"), "1000000");
    await page.getByText("You pay").first().waitFor();
    await t.scroll(350, { ms: 900 });
    await t.zoom(page.getByText("You pay").first().locator("xpath=../.."), { hold: 2800, scale: 1.15 });
    await t.step("This time she pays by card, and sees the charge first", { hold: 300 });
    await t.click(tid(page, "bond-review"));
    await tid(page, "bond-confirm").waitFor();
    await t.read(1800);
    await t.click(tid(page, "bond-confirm"));
    await tid(page, "method-card").waitFor();
    await t.click(tid(page, "method-card"));
    await tid(page, "card-charge").waitFor();
    await t.zoom(tid(page, "card-charge"), { hold: 2800 });
    await t.step("The card is typed on Pesapal's page, never in Finvest", { hold: 300 });
    await t.click(tid(page, "pay-submit"));
    await tid(page, "card-open-page").waitFor({ timeout: 30000 });
    await t.read(2600);
    await t.click(tid(page, "card-open-page"));
    await t.step("Confirmed, with the card it came from", { hold: 300 });
    await t.cut(() => page.getByText("Completed").first().waitFor({ timeout: 90000 }));
    await t.read(1200);
    await t.zoom(page.getByText("Paid by").first().locator("xpath=../.."), { hold: 2600 });
    await t.step("Her bond waits for its first coupon in February", { hold: 300 });
    await t.click(page.getByText("Done", { exact: true }).locator("visible=true").first());
    await t.click(page.locator("[aria-label=Home]").last());
    await tid(page, "bonds-left-out").waitFor();
    await t.zoom(tid(page, "bonds-left-out"), { hold: 2000 });
    await t.click(tid(page, "home-investments"));
    await page.getByText(BOND.name).first().waitFor();
    await t.scroll(400, { ms: 900 });
    await t.read(1200);
    await t.click(page.getByText(BOND.name).first());
    await tid(page, "bond-coupons").waitFor();
    await t.read(2000);
    await t.scroll(400, { ms: 1000 });
    await t.read(2200);
    await t.stop();
  },
};
