import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { WORK } from "../lib/browser.mjs";
import { ensureClientSignedIn } from "../lib/client.mjs";
import { ensureSignedIn, menu } from "../lib/console.mjs";
import { CLIENT } from "../lib/people.mjs";
import { lastSms } from "../lib/session.mjs";

const tid = (page, id) => page.getByTestId(id).locator("visible=true");
const tab = (page, name) => page.locator(`[aria-label=${name}]`).locator("visible=true").last();
const PASSWORD = `${CLIENT.birth.day}${CLIENT.birth.month}${CLIENT.birth.year}`;

/** The newest statement email to the client, and the check code printed in its PDF. */
function statementCode() {
  const dir = path.join(WORK, "mail");
  for (const f of fs.readdirSync(dir).sort().reverse()) {
    const m = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
    const to = Array.isArray(m.to) ? m.to : [m.to];
    if (!to.includes(CLIENT.email) || !m.subject.startsWith("Your Finvest statement")) continue;
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "pv-statement-"));
    try {
      const file = path.join(tmp, "s.pdf");
      fs.writeFileSync(file, Buffer.from(m.attachments[0].content, "base64"));
      const text = execFileSync("pdftotext", ["-upw", PASSWORD, file, "-"], { encoding: "utf8" });
      return text.match(/CHECK CODE\s+([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})/)[1];
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  }
  throw new Error("No statement email");
}

export default {
  id: "18",
  title: "School fees, ready",
  subtitle: "Her bill matures, the money reaches her phone, and a statement proves it.",
  steps: [
    { caption: "Her bill matured overnight and the money is on its way", does: "the payout SMS, Home" },
    { caption: "Activity shows the payout and the interest she earned", does: "Activity: Payout UGX 920,344, interest at maturity" },
    { caption: "The school fees goal rolled into a new term", does: "Goals: Daniel's school fees" },
    { caption: "The team sees what every maturity became", does: "Ruth: Maturities, day by day; Money Movement, the payout paid" },
    { caption: "Esther asks for a signed statement", does: "Statements, Make statement, ready" },
    { caption: "It is emailed, locked with her date of birth", does: "test inbox: the statement email, the PDF, her birth date, the pages" },
    { caption: "Anyone can check it is genuine on finvestug.com", does: "finvestug.com/check, the code from the statement, Check" },
  ],
  takes: [
    { name: "paid", who: "esther-phone", layout: "phone", clock: "+1" },
    { name: "team", who: "ruth", layout: "wide" },
    { name: "statement", who: "esther-phone", layout: "phone" },
    { name: "email", who: "inbox", layout: "mail" },
    { name: "check", who: "visitor", layout: "wide" },
  ],
  async paid(t, page) {
    // The maturity run pays out as the stack starts on the new day: wait for the last text.
    const end = Date.now() + 90000;
    while (!/has been sent to Airtel Money/.test(lastSms(CLIENT.phone)?.text ?? "")) {
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
  async statement(t, page) {
    await ensureClientSignedIn(page, "esther", "/");
    await t.start();
    await t.step("Esther asks for a signed statement", { hold: 800 });
    await t.click(tid(page, "quick-statements"));
    await tid(page, "make-statement").waitFor();
    await t.read(2200);
    await t.click(tid(page, "make-statement"));
    await t.cut(() => tid(page, "statement-ready").waitFor({ timeout: 60000 }));
    await t.zoom(tid(page, "statement-ready"), { hold: 2400 });
    await t.stop();
  },
  async email(t, page) {
    await page.goto("https://mail.local.test/");
    const mail = page.locator("nav a").filter({ hasText: "Your Finvest statement" }).first();
    await mail.waitFor();
    await t.start();
    await t.step("It is emailed, locked with her date of birth", { hold: 800 });
    await t.click(mail);
    await t.read(2400);
    await t.click(page.locator(".att").first());
    await page.getByLabel("Password").waitFor();
    await t.read(1200);
    await t.type(page.getByLabel("Password"), PASSWORD, { delay: 110 });
    await t.click(page.getByRole("button", { name: "Open" }));
    await page.locator("img.page").first().waitFor();
    await t.read(3200);
    await t.scroll(600, { ms: 1600 });
    await t.read(2400);
    await t.scroll(700, { ms: 1600 });
    await t.read(2000);
    await t.stop();
  },
  async check(t, page) {
    const code = statementCode();
    await page.goto("https://finvestug.com/check");
    await page.locator("main input").waitFor();
    await t.start();
    await t.step("Anyone can check it is genuine on finvestug.com", { hold: 800 });
    await t.read(1600);
    await t.type(page.locator("main input"), code, { delay: 90 });
    await t.click(page.locator("main button", { hasText: "Check" }));
    await page.getByText("GENUINE").or(page.getByText("Genuine", { exact: true })).first().waitFor();
    await t.zoom(page.getByText(/Finvest issued a statement with code/).locator("xpath=.."), { hold: 3000 });
    await t.read(1400);
    await t.stop();
  },
};
