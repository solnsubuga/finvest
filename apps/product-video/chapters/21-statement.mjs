import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { WORK } from "../lib/browser.mjs";
import { ensureClientSignedIn } from "../lib/client.mjs";
import { CLIENT } from "../lib/people.mjs";

const tid = (page, id) => page.getByTestId(id).locator("visible=true");
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
  id: "21",
  title: "Proof on paper",
  subtitle: "A signed statement, emailed and checked.",
  steps: [
    { caption: "Esther asks for a signed statement", does: "Statements, Make statement, ready" },
    { caption: "It is emailed, locked with her date of birth", does: "test inbox: the statement email, the PDF, her birth date, the pages" },
    { caption: "Anyone can check it is genuine on finvestug.com", does: "finvestug.com/check, the code from the statement, Check" },
  ],
  takes: [
    { name: "statement", who: "esther-phone", layout: "phone" },
    { name: "email", who: "inbox", layout: "mail" },
    { name: "check", who: "visitor", layout: "wide" },
  ],
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
