// Off camera: finish a member of staff's set-up from their one-time link, with a password and
// an authenticator app, and keep the app's key in $WORK/staff.json for the takes.
//   node setup/enrol-staff.mjs <email> <enrolment link>
import fs from "node:fs";
import path from "node:path";
import { WORK } from "../lib/browser.mjs";
import { codeFor } from "../lib/staff.mjs";
import { openContext, closeBrowser } from "../lib/session.mjs";
import { STAFF_PASSWORD } from "../lib/people.mjs";

const [email, url] = process.argv.slice(2);
const session = await openContext("enrol", "wide");
const page = session.page;
await page.goto(url);
const pw = page.locator("input[type=password]");
await pw.nth(0).fill(STAFF_PASSWORD);
await pw.nth(1).fill(STAFF_PASSWORD);
await page.locator("input[value=authenticator]").check();
await page.waitForTimeout(1500);
const keyText = await page.getByText(/Type this key into the app/).locator("xpath=..").innerText();
const secret = keyText.split("\n").find((l) => /^[A-Z2-7]{4}( [A-Z2-7]{4})+$/.test(l.trim())).replace(/\s+/g, "");
await page.locator("input[autocomplete=one-time-code]").fill(await codeFor(email, secret));
await page.getByRole("button", { name: "Finish set-up" }).click();
await page.waitForURL((u) => !u.hash, { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(2000);
const file = path.join(WORK, "staff.json");
const staff = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : {};
staff[email] = { secret };
fs.writeFileSync(file, JSON.stringify(staff, null, 2));
console.log(email, "enrolled;", (await page.evaluate(() => document.body.innerText)).slice(0, 200).replace(/\n/g, " "));
await closeBrowser();
