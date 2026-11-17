import { ensureSignedIn, confirmIfAsked } from "../lib/console.mjs";
import { ensureClientSignedIn } from "../lib/client.mjs";

// An invented Bank of Uganda issue: a 5 year bond with coupons on 15 Feb and 15 Aug, so the
// first coupon falls on the morning Esther's bill matures. Not a real ISIN or auction result.
export const BOND = {
  code: "BOND-5Y-2031",
  isin: "UG12K1502311",
  name: "5 Year Treasury Bond 2031",
  issued: "2026-02-15",
  matures: "2031-02-15",
  coupon: "14.25",
  price: "101.25",
};

// A second invented issue, added on camera: Esther's 5 year bond is open from before the story
// starts (setup/seed-bond.mjs), so the website and the app show bills and bonds from day one.
export const LONG_BOND = {
  code: "BOND-10Y-2036",
  isin: "UG12K1508361",
  name: "10 Year Treasury Bond 2036",
  issued: "2026-08-15",
  matures: "2036-08-15",
  coupon: "15.50",
  price: "99.80",
};

// An invented product type, proposed and approved on camera.
const TYPE = {
  earns: "Term and rate, paid at maturity or monthly",
  code: "FIXED-DEPOSIT",
  name: "Bank fixed deposit",
  tax: "15",
  description: "A fixed deposit at a Ugandan bank, held for clients.",
};

const approveButton = (page) => page.getByRole("button", { name: "Approve as second person" }).first();

/** Joel proposes a bond from the auction result. */
export async function proposeBond(t, page, bond) {
  await ensureSignedIn(page, "joel", "/products");
  await page.getByText("Treasury bonds", { exact: true }).waitFor();
  await t.start();
  await t.step("A new bond is added like any other product", { hold: 600 });
  await t.click(page.getByRole("link", { name: "New product", exact: true }).first());
  const type = page.getByLabel("Type", { exact: true });
  await t.hover(type);
  await type.selectOption({ label: "Treasury bond" });
  await page.getByLabel("ISIN", { exact: true }).waitFor();
  await t.read(1000);
  await t.step("The ISIN, coupon and dates come from the auction result", { hold: 400 });
  const field = (label) => page.getByLabel(label, { exact: true });
  await t.type(field("Code"), bond.code);
  await t.type(field("ISIN"), bond.isin);
  await t.type(field("Name"), bond.name);
  await t.hover(field("Issue date"));
  await field("Issue date").fill(bond.issued);
  await t.hover(field("Maturity date"));
  await field("Maturity date").fill(bond.matures);
  await t.type(field("Coupon, % a year before tax"), bond.coupon);
  await t.read(600);
  await t.zoom(field("Tax on coupons, %").locator("xpath=.."), { hold: 2000 });
  await t.step("Clients pay today's price for each 100 of face value", { hold: 400 });
  await t.type(field("First price per 100 of face value"), bond.price);
  await t.read(800);
  await t.type(page.locator("main textarea").first(), `Adding the ${bond.name} for clients, from the auction result.`, { delay: 20 });
  await t.click(page.getByRole("button", { name: "Send for approval" }));
  await confirmIfAsked(t, page, "joel");
  await page.getByText("Bond changes waiting for approval").waitFor();
  await page.getByText("Bond changes waiting for approval").scrollIntoViewIfNeeded();
  await t.read(2400);
  await t.stop();
}

/** Ruth approves it as a draft and proposes opening it to clients. */
export async function checkBond(t, page, bond) {
  await ensureSignedIn(page, "ruth", "/products");
  await approveButton(page).waitFor();
  await approveButton(page).scrollIntoViewIfNeeded();
  await t.start();
  await t.step("Ruth checks it, and it is saved as a draft", { hold: 800 });
  await t.zoom(page.getByText("Bond changes waiting for approval").locator("xpath=.."), { hold: 2400 });
  await t.click(approveButton(page));
  await confirmIfAsked(t, page, "ruth");
  // With another bond already listed, pick the new draft so its panel shows.
  const row = page.getByRole("button", { name: bond.name, exact: true });
  await row.waitFor();
  await row.scrollIntoViewIfNeeded();
  await t.click(row);
  await page.getByRole("button", { name: "Propose opening to clients" }).waitFor();
  await t.read(1600);
  await t.step("Opening it to clients is a change of its own", { hold: 400 });
  await page.getByRole("button", { name: "Propose opening to clients" }).scrollIntoViewIfNeeded();
  await t.type(page.locator("main textarea").first(), `Open the ${bond.name} to clients.`, { delay: 25 });
  await t.click(page.getByRole("button", { name: "Propose opening to clients" }));
  await confirmIfAsked(t, page, "ruth");
  await page.getByText("Open to clients").first().waitFor();
  await t.read(2000);
  await t.stop();
}

/** Joel approves the opening, and the bond is live. */
export async function openBond(t, page, bond) {
  await ensureSignedIn(page, "joel", "/products");
  await approveButton(page).waitFor();
  await approveButton(page).scrollIntoViewIfNeeded();
  await t.start();
  await t.step("Joel approves, and the bond is live", { hold: 1000 });
  await t.click(approveButton(page));
  await confirmIfAsked(t, page, "joel");
  const row = page.getByText(`${bond.code} · ${bond.isin}`).locator("xpath=ancestor::tr[1]");
  await row.getByText("Live").waitFor();
  await t.zoom(row, { hold: 2400 });
}

export default {
  id: "06",
  title: "A bond for the long run",
  subtitle: "A new Treasury bond and a new product type, each added by two people.",
  steps: [
    { caption: "A new bond is added like any other product", does: "Joel: Products, New product, Type: Treasury bond" },
    { caption: "The ISIN, coupon and dates come from the auction result", does: "code, ISIN, name, issue and maturity dates, 15.5% coupon; tax fills in at 10%, the rate for 10 years or more (zoom)" },
    { caption: "Clients pay today's price for each 100 of face value", does: "first price 99.80, a reason, Send for approval" },
    { caption: "Ruth checks it, and it is saved as a draft", does: "Ruth: the waiting bond (zoom), Approve as second person" },
    { caption: "Opening it to clients is a change of its own", does: "Ruth: a reason, Propose opening to clients" },
    { caption: "Joel approves, and the bond is live", does: "Joel: Approve as second person; the bond row shows Live (zoom)" },
    { caption: "Every product is built from a type staff can add to", does: "the Product types table (zoom)" },
    { caption: "Joel adds a type for bank fixed deposits", does: "New product type: term and rate, code, name, 15% usual tax, a reason, Send for approval" },
    { caption: "Ruth approves it, and staff can now choose it", does: "Ruth: Approve as second person; the new type row (zoom)" },
    { caption: "Esther can now choose between two bonds", does: "Esther's Invest tab: Treasury bonds, the 5 and 10 year bonds" },
  ],
  takes: [
    { name: "propose", who: "joel", layout: "wide" },
    { name: "check", who: "ruth", layout: "wide" },
    { name: "live", who: "joel", layout: "wide" },
    { name: "typeOk", who: "ruth", layout: "wide" },
    { name: "client", who: "esther-phone", layout: "phone" },
  ],
  propose: (t, page) => proposeBond(t, page, LONG_BOND),
  check: (t, page) => checkBond(t, page, LONG_BOND),
  async live(t, page) {
    await openBond(t, page, LONG_BOND);
    await t.step("Every product is built from a type staff can add to", { hold: 400 });
    const types = page.getByRole("table", { name: "Product types" }).or(page.getByText("TREASURY-BOND · Built in").locator("xpath=ancestor::table[1]")).first();
    await types.scrollIntoViewIfNeeded();
    await t.read(800);
    await t.zoom(types, { hold: 2600, scale: 1.15 });
    await t.step("Joel adds a type for bank fixed deposits", { hold: 400 });
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
    await t.click(page.getByRole("link", { name: "New product type" }));
    const form = page.locator("form[aria-labelledby=new-type]");
    await form.waitFor();
    await t.read(1200);
    await t.click(form.getByLabel(TYPE.earns));
    await t.type(form.getByLabel("Code", { exact: true }), TYPE.code);
    await t.type(form.getByLabel("Name", { exact: true }), TYPE.name);
    await t.type(form.getByLabel("Usual tax, %"), TYPE.tax);
    await t.type(form.getByLabel("Description"), TYPE.description, { delay: 20 });
    await t.type(form.getByLabel("Reason, for the audit trail"), "Clients ask for bank fixed deposits next to bills and bonds.", { delay: 20 });
    await t.read(800);
    await t.click(form.getByRole("button", { name: "Send for approval" }));
    await confirmIfAsked(t, page, "joel");
    await page.getByText("Type changes waiting for approval").waitFor();
    await page.getByText("Type changes waiting for approval").scrollIntoViewIfNeeded();
    await t.read(2200);
    await t.stop();
  },
  async typeOk(t, page) {
    await ensureSignedIn(page, "ruth", "/products");
    const waiting = page.getByText("Type changes waiting for approval");
    await waiting.waitFor();
    await waiting.scrollIntoViewIfNeeded();
    await t.start();
    await t.step("Ruth approves it, and staff can now choose it", { hold: 800 });
    await t.zoom(waiting.locator("xpath=.."), { hold: 2200 });
    await t.click(waiting.locator("xpath=..").getByRole("button", { name: "Approve as second person" }).first());
    await confirmIfAsked(t, page, "ruth");
    const row = page.getByRole("region", { name: "Product types" }).getByRole("row").filter({ hasText: TYPE.code });
    await row.waitFor();
    await row.scrollIntoViewIfNeeded();
    await t.read(800);
    await t.zoom(row, { hold: 2600 });
    await t.stop();
  },
  async client(t, page) {
    await ensureClientSignedIn(page, "esther", "/invest");
    await page.getByText(LONG_BOND.name).first().waitFor();
    await t.start();
    await t.step("Esther can now choose between two bonds", { hold: 600 });
    await t.scroll(500, { ms: 1200 });
    await t.read(1200);
    await t.zoom(page.getByText(LONG_BOND.name).first().locator("xpath=ancestor::*[.//*[contains(., '" + BOND.name + "')]][1]"), { hold: 2600 });
    await t.stop();
  },
};
