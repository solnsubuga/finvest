// Off camera: sign a member of staff in to the console in a saved context.
import { openContext } from "../lib/session.mjs";
import { STAFF } from "../lib/people.mjs";
import { staffCode, STAFF_PASSWORD } from "../lib/staff.mjs";

export async function staffPage(who) {
  const s = await openContext(who, "wide");
  const { page } = s;
  await page.goto("https://admin.finvestug.com/overview");
  await page.getByText(/Staff sign-in|Signed in as/).first().waitFor();
  if (await page.getByText("Staff sign-in").isVisible()) {
    await page.locator("input[type=email]").fill(STAFF[who].email);
    await page.locator("input[type=password]").fill(STAFF_PASSWORD);
    await page.getByRole("button", { name: "Continue" }).click();
    await page.locator("input[autocomplete=one-time-code]").fill(await staffCode(who));
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.getByText("Signed in as").waitFor({ timeout: 15000 }).catch(async (e) => { await page.screenshot({ path: `/tmp/claude-0/pv/login-${who}.png` }); throw e; });
  }
  return s;
}
