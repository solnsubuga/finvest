import path from "node:path";
import { fileURLToPath } from "node:url";
import { ensureSignedIn, confirmIfAsked, menu } from "../lib/console.mjs";

// An invented paper register, as the old business kept it, for a trial import.
const REGISTER = path.join(path.dirname(fileURLToPath(import.meta.url)), "../setup/paper-register");

const button = (scope, text) => scope.locator("button", { hasText: text }).locator("visible=true").first();
const formWith = (page, text) => button(page, text).locator("xpath=ancestor::form[1]");
const potTransfer = (page) => page.getByRole("article", { name: /Transfer of UGX 5,000,000/ });

export default {
  id: "19",
  title: "Three months later",
  subtitle: "The day before her bill matures, the team gets the money ready.",
  steps: [
    { caption: "Three months on, the Overview shows what falls due", does: "Ruth: Overview, cash needed for maturities, the warnings (zoom)" },
    { caption: "If a night's interest run leaves anyone behind, it runs again", does: "Investments, Run again for a date, today, the reason, Send for approval" },
    { caption: "A second person approves, and the interest is posted", does: "Joel: Approve; the investments show interest to today" },
    { caption: "Finvest's own cash pays people on time", does: "Dealing Desk, Record money paid in, UGX 150,000,000, the bank reference" },
    { caption: "The Airtel payout wallet is topped up for tomorrow", does: "Reconciliation, Record a transfer, trust account to ioTec Airtel, UGX 5,000,000" },
    { caption: "Ruth checks both against the bank and approves them", does: "Ruth: Dealing Desk approve and post; Reconciliation approve" },
    { caption: "Reports come out for any period, as a file", does: "Joel: Reports and Audit, Maturity ladder, Download CSV" },
    { caption: "Every action is kept: who did it, when and why", does: "Ruth: Reports and Audit, the audit trail" },
    { caption: "What clients are owed is checked against what is held", does: "Reconciliation: the client money check, waiting for the first closed day" },
    { caption: "Settings hold the rules, and changes need two people", does: "Settings: limits, payment channels with Pesapal cards" },
    { caption: "Paper clients are checked before anyone moves across", does: "Migration: trial import of the paper register, the problems and differences" },
  ],
  takes: [
    { name: "overview", who: "ruth", layout: "wide", clock: "+86" },
    { name: "approve", who: "joel", layout: "wide" },
    { name: "check", who: "ruth", layout: "wide" },
    { name: "reports", who: "joel", layout: "wide" },
    { name: "audit", who: "ruth", layout: "wide" },
  ],
  async overview(t, page) {
    await ensureSignedIn(page, "ruth", "/overview");
    await page.getByText("Cash needed for maturities").waitFor();
    await t.start();
    await t.step("Three months on, the Overview shows what falls due", { hold: 1000 });
    await t.read(1800);
    await t.zoom(page.getByText("Needs attention").locator("xpath=.."), { hold: 2600 });
    await t.scroll(700, { ms: 1400 });
    await t.zoom(page.getByText("Cash needed for maturities").locator("xpath=.."), { hold: 2600 });
    await t.scroll(-700, { ms: 900 });
    await t.step("If a night's interest run leaves anyone behind, it runs again", { hold: 400 });
    await t.click(menu(page, "Investments"));
    await page.getByRole("heading", { name: "Daily interest run" }).waitFor();
    await t.read(2200);
    await t.click(button(page, "Run again for a date"));
    const form = formWith(page, "Send for approval");
    await form.locator('input[placeholder="dd/mm/yyyy"]').fill("2027-02-14");
    await t.type(form.locator("textarea"), "Today's run overlapped with the catch up runs and left investments behind.", { delay: 22 });
    await t.click(button(form, "Send for approval"));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText("Interest run for 14 Feb 2027").waitFor();
    await t.read(1800);
    await t.stop();
  },
  async approve(t, page) {
    await ensureSignedIn(page, "joel", "/investments");
    await page.getByText("Interest run for 14 Feb 2027").waitFor();
    await t.start();
    await t.step("A second person approves, and the interest is posted", { hold: 800 });
    await t.read(1800);
    await t.click(page.locator("button", { hasText: /^Approve$/ }).locator("visible=true"));
    await confirmIfAsked(t, page, "joel");
    await t.cut(async () => {
      for (let i = 0; i < 20 && !(await page.getByText(/to 14 Feb 2027/).first().isVisible()); i++) {
        await page.waitForTimeout(1500);
        await page.reload();
      }
    });
    await page.getByText(/to 14 Feb 2027/).first().waitFor();
    await t.zoom(page.getByText(/to 14 Feb 2027/).first().locator("xpath=ancestor::tr[1]"), { hold: 2400 });

    await t.step("Finvest's own cash pays people on time", { hold: 400 });
    await t.click(menu(page, "Dealing Desk"));
    await page.getByText("Finvest's money in the buffer").waitFor();
    await page.getByText("Finvest's money in the buffer").scrollIntoViewIfNeeded();
    await t.read(2000);
    await t.click(button(page, "Record money paid in"));
    const buffer = formWith(page, "Record for approval");
    await t.type(buffer.getByLabel("Amount (UGX)"), "150000000");
    await t.type(buffer.getByLabel("Bank reference"), "KDB2702140031");
    await buffer.locator('input[placeholder="dd/mm/yyyy"]').fill("2027-02-14");
    await t.type(buffer.getByLabel("Reason"), "Finvest's own money for the liquidity buffer, to its target.", { delay: 22 });
    await t.click(button(buffer, "Record for approval"));
    await confirmIfAsked(t, page, "joel");
    await page.getByText("You recorded this, so someone else has to approve it.").waitFor();
    await t.read(1800);

    await t.step("The Airtel payout wallet is topped up for tomorrow", { hold: 400 });
    await t.click(menu(page, "Reconciliation"));
    const pots = page.getByRole("form", { name: "Record a transfer between pots" });
    await pots.waitFor();
    await pots.scrollIntoViewIfNeeded();
    await t.read(1600);
    await pots.getByLabel("From", { exact: true }).selectOption({ label: "Trust bank account" });
    await t.wait(500);
    await pots.getByLabel("To", { exact: true }).selectOption({ label: "ioTec wallet, Airtel" });
    await t.wait(500);
    await t.type(pots.getByLabel("Sent (UGX)"), "5000000");
    await t.type(pots.getByLabel("Arrived (UGX)"), "5000000");
    await pots.getByLabel("Day the money moved").fill("2027-02-14");
    await t.type(pots.getByLabel("Provider or bank reference"), "KDB2702140044");
    await t.type(pots.getByLabel("Reason"), "Top up the Airtel payout wallet for tomorrow's maturities.", { delay: 22 });
    await t.click(button(pots, "Record transfer"));
    await confirmIfAsked(t, page, "joel");
    await potTransfer(page).waitFor();
    await potTransfer(page).scrollIntoViewIfNeeded();
    await t.read(2200);
    await t.stop();
  },
  async check(t, page) {
    await ensureSignedIn(page, "ruth", "/dealing-desk");
    await button(page, "Approve as second person").waitFor();
    await button(page, "Approve as second person").scrollIntoViewIfNeeded();
    await t.start();
    await t.step("Ruth checks both against the bank and approves them", { hold: 800 });
    await t.read(1600);
    await t.click(button(page, "Approve as second person"));
    await t.read(1600);
    await t.click(button(page, "Yes, approve and post"));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText("Approved and posted").waitFor();
    await t.zoom(page.getByText("Finvest's money in the buffer").locator("xpath=.."), { hold: 2000 });
    await t.click(menu(page, "Reconciliation"));
    await potTransfer(page).waitFor();
    await potTransfer(page).scrollIntoViewIfNeeded();
    await t.read(1600);
    await t.click(button(potTransfer(page), "Approve as second person"));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText("No transfer between pots is waiting for a second person.").waitFor();
    await t.read(1200);
    await t.click(menu(page, "Overview"));
    await page.getByText("Mobile money floats").waitFor();
    await page.getByText("Mobile money floats").scrollIntoViewIfNeeded();
    await t.zoom(page.getByText("Mobile money floats").locator("xpath=.."), { hold: 2400 });
    await t.stop();
  },
  async reports(t, page) {
    await ensureSignedIn(page, "joel", "/overview");
    await t.start();
    await t.step("Reports come out for any period, as a file", { hold: 800 });
    await t.click(menu(page, "Reports and Audit"));
    await page.getByText("Maturity ladder", { exact: true }).waitFor();
    await t.read(2000);
    const card = page
      .getByText("Maturity ladder", { exact: true })
      .locator("xpath=ancestor::*[.//button[normalize-space()='Download']][1]");
    await t.click(button(card, "Download"));
    await t.read(1400);
    const download = page.waitForEvent("download", { timeout: 30000 });
    await t.click(button(card, "Download CSV"));
    const file = await download;
    if (!file.suggestedFilename().endsWith(".csv")) throw new Error("No report file");
    await t.read(1800);
    await t.stop();
  },
  async audit(t, page) {
    await ensureSignedIn(page, "ruth", "/overview");
    await t.start();
    await t.step("Every action is kept: who did it, when and why", { hold: 800 });
    await t.click(menu(page, "Reports and Audit"));
    await page.getByRole("heading", { name: "Audit trail" }).waitFor();
    await page.getByRole("heading", { name: "Audit trail" }).scrollIntoViewIfNeeded();
    await t.read(3000);
    await t.scroll(500, { ms: 1200 });
    await t.read(2600);
    await t.step("What clients are owed is checked against what is held", { hold: 400 });
    await t.click(menu(page, "Reconciliation"));
    await page.getByText("Client money check").first().waitFor();
    await t.read(1200);
    await t.zoom(page.getByText("Client money check").first().locator("xpath=.."), { hold: 2600 });
    await t.step("Settings hold the rules, and changes need two people", { hold: 400 });
    await t.click(menu(page, "Settings"));
    await page.getByText("Limits in force").waitFor();
    await t.read(2400);
    await t.zoom(page.getByText("Channels open").locator("xpath=../.."), { hold: 2600 });
    await t.step("Paper clients are checked before anyone moves across", { hold: 400 });
    await t.click(menu(page, "Migration"));
    await t.click(button(page, "Run a trial import"));
    const files = page.locator("main input[type=file]");
    await files.nth(0).setInputFiles(path.join(REGISTER, "clients.csv"));
    await t.wait(600);
    await files.nth(1).setInputFiles(path.join(REGISTER, "investments.csv"));
    await t.wait(600);
    await page.locator('main input[placeholder="dd/mm/yyyy"]').fill("2027-02-14");
    await t.read(1200);
    await t.click(button(page, "Run the trial"));
    await page.getByText("Problems in the latest trial").waitFor();
    await t.read(2400);
    await page.getByText("What differs").scrollIntoViewIfNeeded();
    await t.zoom(page.getByText("What differs").locator("xpath=.."), { hold: 2800 });
    await t.stop();
  },
};
