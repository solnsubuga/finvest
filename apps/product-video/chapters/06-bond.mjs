import { ensureSignedIn, confirmIfAsked } from "../lib/console.mjs";
import { ensureClientSignedIn } from "../lib/client.mjs";

// An invented Bank of Uganda issue: a 2 year bond with coupons on 15 Feb and 15 Aug, so the
// first coupon falls on the morning Esther's bill matures. Not a real ISIN or auction result.
export const BOND = {
  code: "BOND-2Y-2028",
  isin: "UG12K1502281",
  name: "2 Year Treasury Bond 2028",
  issued: "2026-02-15",
  matures: "2028-02-15",
  coupon: "14.25",
  price: "101.25",
};

const approveButton = (page) => page.getByRole("button", { name: "Approve as second person" }).first();

export default {
  id: "06",
  title: "A bond for the long run",
  subtitle: "A new Treasury bond, added and opened by two people.",
  steps: [
    { caption: "Bonds are added like any other product", does: "Joel: Products, New product, Type: Treasury bond" },
    { caption: "The ISIN, coupon and dates come from the auction result", does: "code, ISIN, name, issue and maturity dates, 14.25% coupon; tax fills in at 20% (zoom)" },
    { caption: "Clients pay today's price for each 100 of face value", does: "first price 101.25, a reason, Send for approval" },
    { caption: "Ruth checks it, and it is saved as a draft", does: "Ruth: the waiting bond (zoom), Approve as second person" },
    { caption: "Opening it to clients is a change of its own", does: "Ruth: a reason, Propose opening to clients" },
    { caption: "Joel approves, and the bond is live", does: "Joel: Approve as second person; the bond row shows Live (zoom)" },
    { caption: "Every product is built from a type staff can add to", does: "the Product types table (zoom)" },
    { caption: "Esther can buy part of the bond at once", does: "Esther's Invest tab: Treasury bonds, the 2 year bond" },
  ],
  takes: [
    { name: "propose", who: "joel", layout: "wide" },
    { name: "check", who: "ruth", layout: "wide" },
    { name: "live", who: "joel", layout: "wide" },
    { name: "client", who: "esther-phone", layout: "phone" },
  ],
  async propose(t, page) {
    await ensureSignedIn(page, "joel", "/products");
    await page.getByText("Treasury bonds", { exact: true }).waitFor();
    await t.start();
    await t.step("Bonds are added like any other product", { hold: 600 });
    await t.click(page.getByRole("link", { name: "New product" }));
    const type = page.getByLabel("Type", { exact: true });
    await t.hover(type);
    await type.selectOption({ label: "Treasury bond" });
    await page.getByLabel("ISIN", { exact: true }).waitFor();
    await t.read(1000);
    await t.step("The ISIN, coupon and dates come from the auction result", { hold: 400 });
    const field = (label) => page.getByLabel(label, { exact: true });
    await t.type(field("Code"), BOND.code);
    await t.type(field("ISIN"), BOND.isin);
    await t.type(field("Name"), BOND.name);
    await t.hover(field("Issue date"));
    await field("Issue date").fill(BOND.issued);
    await t.hover(field("Maturity date"));
    await field("Maturity date").fill(BOND.matures);
    await t.type(field("Coupon, % a year before tax"), BOND.coupon);
    await t.read(600);
    await t.zoom(field("Tax on coupons, %").locator("xpath=.."), { hold: 2000 });
    await t.step("Clients pay today's price for each 100 of face value", { hold: 400 });
    await t.type(field("First price per 100 of face value"), BOND.price);
    await t.read(800);
    await t.type(page.locator("main textarea").first(), "Adding the 2 year bond for clients, from the auction result.", { delay: 20 });
    await t.click(page.getByRole("button", { name: "Send for approval" }));
    await confirmIfAsked(t, page, "joel");
    await page.getByText("Bond changes waiting for approval").waitFor();
    await page.getByText("Bond changes waiting for approval").scrollIntoViewIfNeeded();
    await t.read(2400);
    await t.stop();
  },
  async check(t, page) {
    await ensureSignedIn(page, "ruth", "/products");
    await approveButton(page).waitFor();
    await approveButton(page).scrollIntoViewIfNeeded();
    await t.start();
    await t.step("Ruth checks it, and it is saved as a draft", { hold: 800 });
    await t.zoom(page.getByText("Bond changes waiting for approval").locator("xpath=.."), { hold: 2400 });
    await t.click(approveButton(page));
    await confirmIfAsked(t, page, "ruth");
    await page.getByRole("button", { name: "Propose opening to clients" }).waitFor();
    await t.read(1600);
    await t.step("Opening it to clients is a change of its own", { hold: 400 });
    await page.getByRole("button", { name: "Propose opening to clients" }).scrollIntoViewIfNeeded();
    await t.type(page.locator("main textarea").first(), "Open the 2 year bond to clients.", { delay: 25 });
    await t.click(page.getByRole("button", { name: "Propose opening to clients" }));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText("Open to clients").first().waitFor();
    await t.read(2000);
    await t.stop();
  },
  async live(t, page) {
    await ensureSignedIn(page, "joel", "/products");
    await approveButton(page).waitFor();
    await approveButton(page).scrollIntoViewIfNeeded();
    await t.start();
    await t.step("Joel approves, and the bond is live", { hold: 1000 });
    await t.click(approveButton(page));
    await confirmIfAsked(t, page, "joel");
    const row = page.getByText(`${BOND.code} · ${BOND.isin}`).locator("xpath=ancestor::tr[1]");
    await row.getByText("Live").waitFor();
    await t.zoom(row, { hold: 2400 });
    await t.step("Every product is built from a type staff can add to", { hold: 400 });
    const types = page.getByRole("table", { name: "Product types" }).or(page.getByText("TREASURY-BOND · Built in").locator("xpath=ancestor::table[1]")).first();
    await types.scrollIntoViewIfNeeded();
    await t.read(800);
    await t.zoom(types, { hold: 2600, scale: 1.15 });
    await t.stop();
  },
  async client(t, page) {
    await ensureClientSignedIn(page, "esther", "/invest");
    await page.getByText(BOND.name).first().waitFor();
    await t.start();
    await t.step("Esther can buy part of the bond at once", { hold: 600 });
    await t.scroll(500, { ms: 1200 });
    await t.read(1200);
    await t.zoom(page.getByText(BOND.name).first().locator("xpath=../.."), { hold: 2400 });
    await t.stop();
  },
};
