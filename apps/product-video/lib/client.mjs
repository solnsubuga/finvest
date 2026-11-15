// Client app helpers for takes.
import { PEOPLE } from "./people.mjs";
import { lastSms, nextSms } from "./session.mjs";

const tid = (page, id) => page.getByTestId(id).locator("visible=true");

/**
 * Off camera, before a take: make sure the client is signed in. After the story clock moves on,
 * a web session has ended, as it would. A passkey on the device signs in at once; without one it
 * is the number, the password and a texted code.
 */
export async function ensureClientSignedIn(page, who = "esther", path = "/") {
  const person = PEOPLE[who];
  await page.goto("https://app.finvestug.com/");
  const signedOut = page.getByRole("button", { name: "Log in" }).or(tid(page, "login-passkey"));
  const signedIn = page.locator("[aria-label=Home]").or(page.getByTestId("home"));
  await signedOut.or(signedIn).first().waitFor();
  if (!(await signedOut.first().isVisible())) {
    console.log(`  ${who} is still signed in`);
    if (path !== "/") await page.goto(`https://app.finvestug.com${path}`);
    await page.waitForLoadState("networkidle");
    return;
  }
  if (await page.getByRole("button", { name: "Log in" }).isVisible()) await page.getByRole("button", { name: "Log in" }).click();
  await tid(page, "login-passkey").click();
  const home = page.getByTestId("home").first();
  try {
    await home.waitFor({ timeout: 8000 });
    console.log(`  ${who} signed in with a passkey`);
  } catch {
    console.log(`  ${who} has no working passkey here, so signs in with a texted code`);
    await page.reload();
    await tid(page, "phone-input").fill(person.phone.slice(1));
    await tid(page, "password-input").fill(person.password);
    await tid(page, "login-submit").click();
    const before = lastSms(person.phone);
    await tid(page, "use-code").click();
    const sms = await nextSms(person.phone, before);
    await tid(page, "code-input").fill(sms.code);
    await tid(page, "code-submit").click();
    await home.waitFor();
  }
  if (path !== "/") await page.goto(`https://app.finvestug.com${path}`);
  await page.waitForLoadState("networkidle");
}
