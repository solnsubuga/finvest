// Console helpers for takes.
import { STAFF, STAFF_PASSWORD } from "./people.mjs";
import { staffCode } from "./staff.mjs";

/** Sign in on camera: email, the masked password, then the authenticator code. */
export async function signInOnCamera(t, page, who) {
  await t.type(page.locator("input[type=email]"), STAFF[who].email);
  await t.type(page.locator("input[type=password]"), STAFF_PASSWORD);
  await t.click(page.getByRole("button", { name: "Continue" }));
  const code = await staffCode(who);
  await t.type(page.locator("input[autocomplete=one-time-code]"), code, { delay: 110 });
  await t.click(page.getByRole("button", { name: "Sign in", exact: true }));
  await page.getByText("Signed in as").waitFor();
}

/** Off camera, before a take starts: make sure this member of staff is signed in. */
export async function ensureSignedIn(page, who, path = "/overview") {
  await page.goto(`https://admin.finvestug.com${path}`);
  await page.getByText(/Staff sign-in|Signed in as/).first().waitFor();
  if (await page.getByText("Staff sign-in").isVisible()) {
    await page.locator("input[type=email]").fill(STAFF[who].email);
    await page.locator("input[type=password]").fill(STAFF_PASSWORD);
    await page.getByRole("button", { name: "Continue" }).click();
    await page.locator("input[autocomplete=one-time-code]").fill(await staffCode(who));
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.getByText("Signed in as").waitFor();
    if (!page.url().endsWith(path)) await page.goto(`https://admin.finvestug.com${path}`);
  }
  await page.waitForLoadState("networkidle");
}

export const menu = (page, name) => page.getByRole("link", { name, exact: true });

/**
 * Decisions ask the member of staff to confirm it is them when their last second step is more
 * than ten minutes old. Answer it on camera when it appears.
 */
export async function confirmIfAsked(t, page, who, { wait = 2500 } = {}) {
  const dialog = page.getByText("Confirm it is you");
  try {
    await dialog.waitFor({ timeout: wait });
  } catch {
    return false;
  }
  const field = page.getByRole("dialog").getByLabel("6-digit code").or(page.getByLabel("6-digit code")).last();
  await t.type(field, await staffCode(who), { delay: 110 });
  await t.click(page.getByRole("button", { name: "Confirm", exact: true }));
  await dialog.waitFor({ state: "hidden" });
  return true;
}
