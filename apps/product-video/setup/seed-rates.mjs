// Off camera, a week before the story starts: two staff put the 91 and 182 day bills on the
// Bank of Uganda auction of 21 Oct 2026 (issue 1236), with 20% withholding tax, so the website
// and the app show real figures from day one. A rate starts the day after it is approved.
import { closeBrowser } from "../lib/session.mjs";
import { RATES } from "../lib/people.mjs";
import { staffPage } from "./staff-login.mjs";

const joel = await staffPage("joel");
const ruth = await staffPage("ruth");
for (const r of RATES.filter((x) => x.code !== "TREA-364")) {
  const p = joel.page;
  await p.goto("https://admin.finvestug.com/rates");
  await p.getByLabel("Product", { exact: true }).selectOption({ label: r.name });
  await p.getByLabel("Rate before tax, %").fill(r.rate);
  await p.getByLabel("Tax, %", { exact: true }).fill(r.tax);
  const tomorrow = await p.evaluate(() => { const d = new Date(Date.now() + 86400000); return d.toISOString().slice(0, 10); });
  await p.getByLabel("Starts on").fill(tomorrow);
  await p.getByLabel("Reason, for the audit trail").fill(r.reason);
  await p.getByRole("button", { name: "Send for approval" }).click();
  await p.getByText(/Waiting for approval/).first().waitFor();
  await p.waitForTimeout(1500);

  const q = ruth.page;
  await q.goto("https://admin.finvestug.com/rates");
  await q.waitForLoadState("networkidle");
  await q.getByRole("button", { name: /^Approve/ }).first().click();
  const yes = q.getByRole("button", { name: /Yes, approve|Approve change|Confirm/ });
  if (await yes.count()) await yes.first().click();
  await q.waitForTimeout(2000);
  console.log("rate approved", r.code, r.rate);
}
await joel.close();
await ruth.close();
await closeBrowser();
