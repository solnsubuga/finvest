import { signInOnCamera, ensureSignedIn, menu, confirmIfAsked } from "../lib/console.mjs";
import { RATES } from "../lib/people.mjs";

const bill = RATES.find((r) => r.code === "TREA-364");

async function approve(t, page) {
  await ensureSignedIn(page, "ruth", "/products");
  await page.getByRole("button", { name: "Approve as second person" }).waitFor();
  await t.start();
}

export default {
  id: "05",
  title: "Setting this week's rates",
  subtitle: "Real auction yields, approved by two people.",
  steps: [
    { caption: "Finance staff keep rates in line with each auction", does: "Joel signs in, Rates: the 21 Oct 2026 auction (zoom)" },
    { caption: "Joel proposes a new 364 day bill", does: "Products, New product: TREA-364, 364 Day Treasury Bill, 364 days, UGX 500,000" },
    { caption: "The auction yield goes in before tax", does: "14.5% before tax, 20% tax; clients see 11.60% (zoom)" },
    { caption: "Nothing changes until a second person agrees", does: "reason, Send for approval, the waiting card" },
    { caption: "Ruth checks it and approves it as a draft", does: "Ruth: Products, Approve as second person" },
    { caption: "Opening it to clients is a change of its own", does: "Joel: Edit, Open to clients with a reason, Send opening for approval" },
    { caption: "Ruth approves again, and the bill is live", does: "Ruth approves; the table shows Live" },
    { caption: "Clients can choose the new bill at once", does: "Esther's Invest tab lists the 364 day bill" },
  ],
  takes: [
    { name: "propose", who: "joel", layout: "wide" },
    { name: "approve", who: "ruth", layout: "wide" },
    { name: "open", who: "joel", layout: "wide" },
    { name: "live", who: "ruth", layout: "wide" },
    { name: "site", who: "esther-phone", layout: "phone" },
  ],
  async propose(t, page) {
    await page.goto("https://admin.finvestug.com/");
    await page.getByText("Staff sign-in").waitFor();
    await t.start();
    await t.step("Finance staff keep rates in line with each auction", { hold: 600 });
    await t.fast(() => signInOnCamera(t, page, "joel"), 2);
    await t.click(menu(page, "Rates"));
    await page.getByText("Last bill auction").waitFor();
    await t.zoom(page.getByText("Last bill auction").locator("xpath=.."), { hold: 2600 });
    await t.read(1200);
    await t.step("Joel proposes a new 364 day bill", { hold: 500 });
    await t.click(menu(page, "Products"));
    await t.click(page.getByRole("link", { name: "New product" }));
    const type = page.getByLabel("Type", { exact: true });
    await t.hover(type);
    await type.selectOption({ label: "Treasury bill" });
    await t.read(700);
    await t.type(page.getByLabel("Code"), bill.code);
    await t.type(page.getByLabel("Name"), bill.name);
    await t.type(page.getByLabel("Term, days"), "364");
    await t.type(page.getByLabel("Minimum, UGX"), "500000");
    await t.step("The auction yield goes in before tax", { hold: 500 });
    await t.type(page.getByLabel("Yearly rate before tax, %"), bill.rate);
    await t.type(page.getByLabel("Tax on interest, %"), bill.tax);
    await t.zoom(page.getByText("What a client sees").locator("xpath=.."), { hold: 2400 });
    await t.step("Nothing changes until a second person agrees", { hold: 400 });
    await t.type(page.getByLabel("Reason, for the audit trail"), bill.reason, { delay: 20 });
    await t.click(page.getByRole("button", { name: "Send for approval" }));
    await confirmIfAsked(t, page, "joel");
    await page.getByText("waiting for a second person").waitFor();
    await t.read(2600);
    await t.stop();
  },
  async approve(t, page) {
    await approve(t, page);
    await t.step("Ruth checks it and approves it as a draft", { hold: 1800 });
    await t.click(page.getByRole("button", { name: "Approve as second person" }));
    await confirmIfAsked(t, page, "ruth");
    await page.getByRole("cell", { name: /364 Day Treasury Bill/ }).or(page.getByText("TREA-364 · 364 days")).first().waitFor();
    await t.read(2000);
    await t.stop();
  },
  async open(t, page) {
    await ensureSignedIn(page, "joel", "/products");
    await t.start();
    await t.step("Opening it to clients is a change of its own", { hold: 600 });
    await t.click(page.getByRole("button", { name: "364 Day Treasury Bill" }));
    await t.click(page.getByRole("link", { name: "Edit 364 Day Treasury Bill" }));
    await page.getByText("Open to clients").waitFor();
    await page.getByText("Open to clients").scrollIntoViewIfNeeded();
    await t.read(1200);
    await t.type(page.locator("textarea").last(), "Open the 364 day bill at the 21 Oct auction yield", { delay: 20 });
    await t.click(page.getByRole("button", { name: "Send opening for approval" }));
    await confirmIfAsked(t, page, "joel");
    await t.wait(1500);
    await t.read(1500);
    await t.stop();
  },
  async live(t, page) {
    await approve(t, page);
    await t.step("Ruth approves again, and the bill is live", { hold: 1400 });
    await t.click(page.getByRole("button", { name: "Approve as second person" }));
    await confirmIfAsked(t, page, "ruth");
    await t.wait(1500);
    await t.zoom(page.getByText("TREA-364 · 364 days").locator("xpath=ancestor::tr"), { hold: 2400 });
    await t.stop();
  },
  async site(t, page) {
    await page.goto("https://app.finvestug.com/invest");
    await page.getByText("364 Day Treasury Bill").first().waitFor();
    await t.start();
    await t.step("Clients can choose the new bill at once", { hold: 1200 });
    await t.read(1200);
    await t.hover(page.getByText("364 Day Treasury Bill").first());
    await t.read(2400);
    await t.stop();
  },
};
