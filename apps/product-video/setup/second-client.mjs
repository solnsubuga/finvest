// Off camera: sign Moses Okello up through the real app with the same steps the chapters show
// for Esther, hand in his ID check, and have Ruth approve it in the console.
//   PV_PERSON=moses node setup/second-client.mjs
import { WORK } from "../lib/browser.mjs";
import { openContext, closeBrowser } from "../lib/session.mjs";
import { QuietTake } from "../lib/quiet-take.mjs";
import { CLIENT } from "../lib/people.mjs";
import { ensureSignedIn, confirmIfAsked } from "../lib/console.mjs";
import intro from "../chapters/01-introducing.mjs";
import safe from "../chapters/02-safe.mjs";
import idCheck from "../chapters/03-id-check.mjs";

if (CLIENT.given !== "Moses") throw new Error("Run with PV_PERSON=moses");

async function run(chapter, name, who, layout) {
  console.log(`second client: ${chapter.id} ${name}`);
  const s = await openContext(who, layout);
  const t = new QuietTake(s.page, { chapter, name, layout });
  // A picture every few seconds, so a stuck step can be seen.
  const watch = setInterval(() => s.page.screenshot({ path: `${WORK}/second-client-now.png`, timeout: 3000 }).catch(() => {}), 4000);
  try {
    await chapter[name](t, s.page, s);
  } catch (error) {
    await s.page.screenshot({ path: `${WORK}/second-client-failed.png` });
    throw error;
  } finally {
    clearInterval(watch);
  }
  await s.close();
}

await run(intro, "signup", "moses-phone", "phone");
await run(safe, "passkey", "moses-phone", "phone");
await run(safe, "email", "moses-inbox", "mail");
await run(idCheck, "check", "moses-phone", "phone");

const s = await openContext("ruth", "wide");
const t = new QuietTake(s.page, { chapter: { id: "setup" }, layout: "wide" });
const page = s.page;
await ensureSignedIn(page, "ruth", "/identity-checks");
// The automatic checks take a moment after hand-in; the decision form shows once they finish.
for (let i = 0; i < 12 && !(await page.getByLabel("Reason for your decision").isVisible()); i++) {
  await page.waitForTimeout(5000);
  await page.reload();
  await page.getByText(/READ FROM THE ID|waiting/).first().waitFor().catch(() => {});
}
await page.getByText(`${CLIENT.given} ${CLIENT.family}`, { exact: false }).first().waitFor();
await page.getByLabel("Reason for your decision").fill("ID photos and details match. Automatic checks passed.");
await page.getByRole("button", { name: "Approve" }).click();
await page.getByRole("button", { name: "Yes, approve" }).click();
await confirmIfAsked(t, page, "ruth");
await page.getByText(/Account number .* was issued/).waitFor();
await s.close();
await closeBrowser();
console.log(`${CLIENT.given} ${CLIENT.family} is signed up and approved`);
