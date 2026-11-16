import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WORK } from "../lib/browser.mjs";
import { ensureSignedIn, menu, confirmIfAsked } from "../lib/console.mjs";
import { PEOPLE } from "../lib/people.mjs";
import { drawReceipt } from "../lib/receipt.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const tid = (page, id) => page.getByTestId(id).locator("visible=true");
const MOSES = PEOPLE.moses;
// The trust account's details, invented for the demo.
export const TRUST = { bank: "Demo Bank Uganda", name: "Finvest Clients Trust Account", number: "9030005128471", branch: "Kampala Road" };
const SENT_ON = "2026-11-15";
const STATEMENT = path.join(WORK, "documents-bank", `trust-statement-${SENT_ON}.csv`);
const RECEIPT_NO = "KDB2611150417";

async function bankSection(page) {
  const section = page.locator("section[aria-labelledby=bank-accounts]");
  await page.getByText("Bank account for transfers").scrollIntoViewIfNeeded();
  return section;
}

export default {
  id: "11",
  title: "Paying by bank transfer",
  subtitle: "Every shilling matched to the bank statement, by two people.",
  steps: [
    { caption: "Bank transfers go to Finvest's client trust account", does: "Ruth: Settings, Bank account for transfers" },
    { caption: "Ruth enters the account from the bank's letter", does: "Set the account: bank, name, number, branch, reason, Send for approval" },
    { caption: "Joel checks every detail and approves it", does: "Joel: Settings, Approve, what he checked, Approve the account" },
    { caption: "Moses, a shopkeeper, invests in the 364 day bill", does: "Moses's phone: Invest, 364 Day Treasury Bill, UGX 2,000,000, Roll over" },
    { caption: "He pays by bank, with his account number as reference", does: "Confirm and pay, Pay by bank transfer, read the details" },
    { caption: "He sends a photo of his bank's receipt", does: "I have sent it, choose the receipt, amount, day, bank, number, Submit" },
    { caption: "Next morning, Joel imports the trust account statement", does: "Joel, a day later: Reconciliation, Import the statement (CSV)" },
    { caption: "The receipt is matched to its statement line", does: "Bank transfers to check: pick the line, Match to this line" },
    { caption: "Ruth approves, and Moses's investment starts", does: "Ruth: Approve as second person" },
  ],
  takes: [
    { name: "account", who: "ruth", layout: "wide" },
    { name: "approve", who: "joel", layout: "wide" },
    { name: "moses", who: "moses-phone", layout: "phone" },
    { name: "statement", who: "joel", layout: "wide", clock: "+1" },
    { name: "confirm", who: "ruth", layout: "wide" },
  ],
  // Moses joins off camera, through the real app, with the steps chapters 1 to 4 show for Esther.
  async before() {
    for (const f of ["state-moses-phone.json", "state-moses-inbox.json", "passkeys-moses.json", "passkeys-moses-phone.json"]) fs.rmSync(path.join(WORK, f), { force: true });
    execFileSync("node", [path.join(here, "../setup/second-client.mjs")], { stdio: "inherit", env: { ...process.env, PV_PERSON: "moses" } });
    fs.mkdirSync(path.dirname(STATEMENT), { recursive: true });
  },
  async account(t, page) {
    await ensureSignedIn(page, "ruth", "/settings");
    await page.getByText("Bank account for transfers").waitFor();
    await t.start();
    await t.read(600);
    await t.step("Bank transfers go to Finvest's client trust account", { hold: 600 });
    await t.fast(() => t.scroll(1900, { ms: 1800 }), 3);
    const section = await bankSection(page);
    await t.read(2200);
    await t.step("Ruth enters the account from the bank's letter", { hold: 400 });
    await t.click(section.getByRole("button", { name: "Set the account" }).first());
    const f = page.getByRole("form", { name: /New UGX account details/ });
    await t.type(f.getByLabel("Bank", { exact: true }), TRUST.bank, { delay: 30 });
    await t.type(f.getByLabel(/^Account name/), TRUST.name, { delay: 30 });
    await t.type(f.getByLabel(/^Account number/), TRUST.number, { delay: 60 });
    await t.type(f.getByLabel(/^Branch/), TRUST.branch, { delay: 30 });
    await t.type(f.getByLabel(/^Why is it changing/), "From the bank's account opening letter of 2 Nov 2026.", { delay: 25 });
    await t.click(f.getByRole("button", { name: "Send for approval" }));
    await confirmIfAsked(t, page, "ruth");
    await section.getByText("Someone else must approve or reject your change.").waitFor();
    await t.zoom(section.getByText("Someone else must approve or reject your change."), { hold: 2000 });
    await t.stop();
  },
  async approve(t, page) {
    await ensureSignedIn(page, "joel", "/settings");
    const section = await bankSection(page);
    await section.getByRole("button", { name: "Approve", exact: true }).waitFor();
    await section.getByRole("button", { name: "Approve", exact: true }).scrollIntoViewIfNeeded();
    await t.start();
    await t.step("Joel checks every detail and approves it", { hold: 1800 });
    await t.click(section.getByRole("button", { name: "Approve", exact: true }));
    await t.type(section.getByLabel("What did you check it against?"), "Checked every detail against the bank's letter of 2 Nov 2026.", { delay: 25 });
    await t.click(section.getByRole("button", { name: "Approve the account" }));
    await confirmIfAsked(t, page, "joel");
    await section.getByText("In use").first().waitFor();
    await t.read(2400);
    await t.stop();
  },
  async moses(t, page) {
    await page.goto("https://app.finvestug.com/");
    await page.getByTestId("home").waitFor();
    await t.start();
    await t.step("Moses, a shopkeeper, invests in the 364 day bill", { hold: 1400 });
    await t.click(page.locator("[aria-label=Invest]").last());
    await t.click(page.getByText("364 Day Treasury Bill").first());
    await t.type(tid(page, "amount"), "2000000");
    await page.getByText("You get back").first().waitFor();
    await t.read(2000);
    await t.click(tid(page, "review"));
    await t.click(tid(page, "at-maturity-roll_over"));
    await t.read(1000);
    await t.step("He pays by bank, with his account number as reference", { hold: 400 });
    await t.click(tid(page, "confirm"));
    await t.click(tid(page, "pay-by-bank"));
    await tid(page, "bank-sent").waitFor();
    await t.read(1800);
    const reference = (await page.getByRole("button", { name: "Copy reference" }).locator("xpath=..").innerText()).match(/\d{6,}/)[0];
    await t.zoom(page.getByText(`Put ${reference} as the reference`), { hold: 2200 });
    await t.scroll(300, { ms: 800 });
    await t.read(1600);
    // Off camera, Moses's bank gives him its receipt: the invented bank's slip, with the
    // reference the app just showed him.
    const receipt = await drawReceipt(path.join(WORK, MOSES.documents, "bank-receipt.jpg"), {
      bank: "Kampala Demo Bank", payer: "MOSES OKELLO", toName: TRUST.name, toNumber: TRUST.number,
      amount: "UGX 2,000,000", day: "15 Nov 2026", number: RECEIPT_NO, reference,
    });
    fs.writeFileSync(STATEMENT, `Date,Details,Reference,In,Out\n${SENT_ON},MOSES OKELLO ${reference},${RECEIPT_NO},2000000,\n`);
    await t.step("He sends a photo of his bank's receipt", { hold: 400 });
    await t.click(tid(page, "bank-sent"));
    const chooser = page.waitForEvent("filechooser");
    await t.click(tid(page, "receipt-choose"));
    await (await chooser).setFiles(receipt);
    await tid(page, "receipt-file").waitFor();
    await t.read(900);
    await t.type(tid(page, "receipt-amount"), "2000000");
    await t.type(tid(page, "receipt-day"), "15/11/2026");
    await t.type(tid(page, "receipt-bank"), "Kampala Demo Bank");
    await t.type(tid(page, "receipt-number"), RECEIPT_NO);
    await t.click(tid(page, "receipt-submit"));
    await page.getByText("We have your receipt").waitFor();
    await t.read(3200);
    await t.stop();
  },
  async statement(t, page) {
    await ensureSignedIn(page, "joel", "/reconciliation");
    await page.getByText("Close the day").first().waitFor();
    await t.start();
    await t.step("Next morning, Joel imports the trust account statement", { hold: 1400 });
    const pot = page.getByText(/^Trust bank account/).locator("xpath=ancestor::*[.//button[normalize-space()='Import the statement']][1]");
    await t.click(pot.getByRole("button", { name: "Import the statement" }));
    const f = page.getByRole("form", { name: /Import the statement for Trust bank account/ });
    await t.read(1400);
    await t.type(f.getByLabel("Opening balance"), "0");
    await t.type(f.getByLabel("Closing balance"), "2000000");
    await f.getByLabel("Statement file").setInputFiles(STATEMENT);
    await t.read(600);
    await t.click(f.getByRole("button", { name: "Import", exact: true }));
    await page.getByText(/Statement imported/).waitFor();
    await t.read(1200);
    await t.zoom(page.getByText("On the statement, not in the ledger").locator("xpath=../.."), { hold: 2000 });
    await t.step("The receipt is matched to its statement line", { hold: 300 });
    await page.reload();
    await page.getByText("Find it on the bank statement").waitFor();
    await page.locator("main").evaluate((m) => m.closest("[class*=overflow]")?.scrollTo?.(0, 0));
    await t.read(2400);
    const form = page.getByRole("form", { name: "Match to the bank statement" });
    await t.click(form.getByRole("radio").first());
    await t.read(1200);
    await t.click(form.getByRole("button", { name: "Match to this line" }));
    await confirmIfAsked(t, page, "joel");
    await page.getByText(/A second person has to approve it/).waitFor();
    await t.read(2600);
    await t.stop();
  },
  async confirm(t, page) {
    await ensureSignedIn(page, "ruth", "/reconciliation");
    await page.getByRole("button", { name: "Approve as second person" }).waitFor();
    await t.start();
    await t.step("Ruth approves, and Moses's investment starts", { hold: 1500 });
    await t.read(1800);
    await t.click(page.getByRole("button", { name: "Approve as second person" }));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText(/Approved\. Finvest is recording the money/).or(page.getByText("Nothing is waiting")).first().waitFor();
    await t.read(2800);
    await t.stop();
  },
};
