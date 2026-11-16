import { ensureClientSignedIn } from "../lib/client.mjs";
import { ensureSignedIn, menu, confirmIfAsked } from "../lib/console.mjs";
import { CLIENT } from "../lib/people.mjs";
import { lastSms } from "../lib/session.mjs";
import { BOND } from "./06-bond.mjs";
import { WORK } from "../lib/browser.mjs";
import fs from "node:fs";
import path from "node:path";

/** How many texts to a number say a payout reached Airtel Money, since the stack started. */
function sentCount(phone) {
  const log = fs.readFileSync(path.join(WORK, "logs/worker.log"), "utf8");
  const wanted = phone.replace(/^0/, "+256");
  return [...log.matchAll(/\[local SMS to (\+\d+)\] (.*)/g)].filter((m) => m[1] === wanted && /has been sent to Airtel Money/.test(m[2])).length;
}

const tid = (page, id) => page.getByTestId(id).locator("visible=true");
const tab = (page, name) => page.locator(`[aria-label=${name}]`).locator("visible=true").last();

export default {
  id: "20",
  title: "School fees, ready",
  subtitle: "Her bill matures, her bond pays, and the money reaches her phone.",
  steps: [
    { caption: "Her bill matured overnight and the money is on its way", does: "the payout SMS, Home" },
    { caption: "Activity shows the payout and the interest she earned", does: "Activity: Payout UGX 920,344, interest at maturity" },
    { caption: "The school fees goal rolled into a new term", does: "Goals: Daniel's school fees" },
    { caption: "Her bond paid its first coupon the same morning", does: "Portfolio, the bond: Coupons, 15 Feb 2027, UGX 57,000 paid to her account (zoom)" },
    { caption: "For the rest of the fees, she sells the bond early", does: "Sell early, read, her Airtel number, Ask to sell" },
    { caption: "Joel records the price the desk sold it at", does: "Joel: Dealing Desk, Bonds clients are selling, Record price: 101.5, today, the desk reference" },
    { caption: "Ruth approves, and Esther is paid that day", does: "Ruth: Approve; Approved, being paid" },
    { caption: "The money from the sale reaches her phone", does: "Esther: the payout SMS; the bond shows Sold for UGX 1,015,000 (zoom)" },
    { caption: "The team sees what every maturity became", does: "Ruth: Maturities, day by day; Money Movement, the payout paid" },
  ],
  takes: [
    { name: "paid", who: "esther-phone", layout: "phone", clock: "+1" },
    { name: "sell", who: "esther-phone", layout: "phone" },
    { name: "price", who: "joel", layout: "wide" },
    { name: "approve", who: "ruth", layout: "wide" },
    { name: "sold", who: "esther-phone", layout: "phone" },
    { name: "team", who: "ruth", layout: "wide" },
  ],
  async paid(t, page) {
    // The maturity and coupon runs pay out as the stack starts on the new day: wait for both texts.
    const end = Date.now() + 120000;
    while (sentCount(CLIENT.phone) < 2) {
      if (Date.now() > end) throw new Error("The payout was not sent");
      await new Promise((r) => setTimeout(r, 1000));
    }
    await ensureClientSignedIn(page, "esther", "/");
    await t.start();
    await t.step("Her bill matured overnight and the money is on its way", { hold: 600 });
    await t.sms(lastSms(CLIENT.phone).text);
    await t.read(1800);
    await t.step("Activity shows the payout and the interest she earned", { hold: 300 });
    await t.click(tab(page, "Activity"));
    await page.getByText("+UGX 920,344").first().waitFor();
    await t.zoom(page.getByText("+UGX 920,344").first().locator("xpath=../.."), { hold: 2400 });
    await t.read(1400);
    await t.step("The school fees goal rolled into a new term", { hold: 300 });
    await t.click(tab(page, "Goals"));
    await page.getByText("Daniel's school fees").first().waitFor();
    await t.read(1600);
    await t.click(page.getByText("Daniel's school fees").first());
    await t.read(3000);
    await t.stop();
  },
  async sell(t, page) {
    await ensureClientSignedIn(page, "esther", "/portfolio");
    await page.getByText(BOND.name).first().waitFor();
    await t.start();
    await t.step("Her bond paid its first coupon the same morning", { hold: 600 });
    await t.scroll(400, { ms: 900 });
    await t.click(page.getByText(BOND.name).first());
    await tid(page, "bond-coupons").waitFor();
    await tid(page, "bond-coupons").scrollIntoViewIfNeeded();
    await t.read(800);
    await t.zoom(tid(page, "bond-coupons"), { hold: 2600 });
    await t.step("For the rest of the fees, she sells the bond early", { hold: 300 });
    await t.click(tid(page, "bond-sell"));
    await tid(page, "confirm-sell").waitFor();
    await t.read(3200);
    await t.scroll(400, { ms: 900 });
    await t.read(1400);
    await t.click(tid(page, "confirm-sell"));
    await tid(page, "bond-sale").waitFor();
    await t.zoom(tid(page, "bond-sale"), { hold: 2400 });
    await t.stop();
  },
  async price(t, page) {
    await ensureSignedIn(page, "joel", "/dealing-desk");
    const sec = page.getByText("Bonds clients are selling").locator("xpath=ancestor::section[1]");
    await sec.getByRole("button", { name: /^Record price for the sale/ }).waitFor();
    await sec.scrollIntoViewIfNeeded();
    await t.start();
    await t.step("Joel records the price the desk sold it at", { hold: 800 });
    await t.read(1600);
    await t.click(sec.getByRole("button", { name: /^Record price for the sale/ }));
    await t.type(sec.getByLabel("Price per 100"), "101.5");
    await t.hover(sec.getByLabel("Day it settled"));
    await sec.getByLabel("Day it settled").fill("2027-02-15");
    await t.type(sec.getByLabel("Desk reference"), "DESK-2702150017, sold to a pension fund", { delay: 25 });
    await t.click(sec.getByRole("button", { name: "Record price", exact: true }));
    await confirmIfAsked(t, page, "joel");
    await sec.getByText("Waiting for a second person").waitFor();
    await t.read(2200);
    await t.stop();
  },
  async approve(t, page) {
    await ensureSignedIn(page, "ruth", "/dealing-desk");
    const sec = page.getByText("Bonds clients are selling").locator("xpath=ancestor::section[1]");
    await sec.getByRole("button", { name: /^Approve the sale/ }).waitFor();
    await sec.scrollIntoViewIfNeeded();
    await t.start();
    await t.step("Ruth approves, and Esther is paid that day", { hold: 800 });
    await t.read(1800);
    await t.click(sec.getByRole("button", { name: /^Approve the sale/ }));
    await confirmIfAsked(t, page, "ruth");
    await sec.getByText(/Approved, being paid|Sold/).first().waitFor();
    await t.zoom(sec, { hold: 2400, scale: 1.15 });
    await t.stop();
  },
  async sold(t, page) {
    await ensureClientSignedIn(page, "esther", "/portfolio");
    const end = Date.now() + 120000;
    while (sentCount(CLIENT.phone) < 3) {
      if (Date.now() > end) throw new Error("The sale was not paid out");
      await new Promise((r) => setTimeout(r, 1000));
    }
    await page.getByText(BOND.name).first().waitFor();
    await t.start();
    await t.step("The money from the sale reaches her phone", { hold: 600 });
    await t.sms(lastSms(CLIENT.phone).text);
    await t.scroll(400, { ms: 900 });
    await t.click(page.getByText(BOND.name).first());
    await page.getByText(/Sold for UGX/).first().waitFor();
    await t.zoom(page.getByText(/Sold for UGX/).first().locator("xpath=.."), { hold: 2600 });
    await t.stop();
  },
  async team(t, page) {
    await ensureSignedIn(page, "ruth", "/overview");
    await t.start();
    await t.step("The team sees what every maturity became", { hold: 800 });
    await t.click(menu(page, "Maturities").or(page.getByRole("link", { name: /^Maturities/ })).first());
    await page.getByText("Day by day").first().waitFor();
    await t.read(2200);
    await t.zoom(page.getByText("Day by day").first().locator("xpath=.."), { hold: 2600 });
    await t.click(menu(page, "Money Movement"));
    await page.getByText("−UGX 920,344").first().waitFor();
    await t.zoom(page.getByText("−UGX 920,344").first().locator("xpath=ancestor::tr[1]"), { hold: 2400 });
    await t.stop();
  },
};
