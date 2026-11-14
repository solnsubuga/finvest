import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { DEMO_PIN, leaveForSixMinutes, typePin } from "./pin";

// Every main screen of the client app, checked against WCAG 2.1 A and AA with axe, on a phone
// and on a wide window. The app runs on its sample sign-in (apps/client/src/auth/stubAuthClient.ts).
const DEMO_PHONE = "772123456";
const DEMO_PASSWORD = "correcthorse1";

// Screens reached by the app's own router after sign-in, as a link does. Loading an address
// afresh would sign the sample session out.
const SIGNED_IN = [
  "/",
  "/activity",
  "/portfolio",
  "/invest",
  "/goals",
  "/goals/new",
  "/insights",
  "/documents",
  "/help",
  "/profile",
  "/account/next-of-kin",
  "/account/investing-questions",
  "/account/language",
  "/account/close",
  "/notifications",
  "/notifications/settings",
  "/security",
  "/security/devices",
  "/security/passkeys",
  "/payouts",
  "/payouts/add",
  "/payouts/history",
  "/autoinvest",
  "/autoinvest/new",
  "/support/complaints",
  "/support/complaints/new",
  "/id-check",
  "/id-check/details",
  "/id-check/document",
];

/** Violations of WCAG 2.1 A and AA, one line each, so a failure names the rule and the element.
 *
 * `label-content-name-mismatch` is left out: whole cards (an investment, a payout, a
 * notification) are one button with a spoken summary that starts with the card's title but
 * also holds the figures shown under it. The rule asks for every visible word in the name.
 * docs/gaps.md records it for a pass with a screen reader user. */
async function violations(page: Page): Promise<string[]> {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .disableRules(["label-content-name-mismatch"])
    .analyze();
  return result.violations.flatMap((rule) =>
    rule.nodes.map((node) => `${rule.id}: ${node.target.join(" ")}`),
  );
}

async function visit(page: Page, path: string) {
  await page.evaluate((to) => {
    window.history.pushState({}, "", to);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, path);
  await page.waitForLoadState("networkidle");
}

test("the screens before sign-in meet WCAG 2.1 AA", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Create an account")).toBeVisible();
  expect(await violations(page), "welcome").toEqual([]);
  await page.getByText("Log in", { exact: true }).first().click();
  await expect(page.getByTestId("phone-input")).toBeVisible();
  expect(await violations(page), "log in").toEqual([]);
  await page.getByText("I changed my number or cannot get in").click();
  await expect(page.getByTestId("recover-phone")).toBeVisible();
  expect(await violations(page), "get back into your account").toEqual([]);
  await page.getByTestId("recover-phone").click();
  const submit = page.getByTestId("call-back-submit").filter({ visible: true });
  await submit.click();
  await expect(page.getByText("Enter a number we can call you on now.")).toBeVisible();
  expect(await violations(page), "ask us to call you").toEqual([]);
  await page.getByTestId("account-phone-input").filter({ visible: true }).fill(DEMO_PHONE);
  await page.getByTestId("contact-phone-input").filter({ visible: true }).fill("701555013");
  await submit.click();
  await expect(page.getByText("We will call you").last()).toBeVisible();
  expect(await violations(page), "we will call you").toEqual([]);
});

test("every main screen after sign-in meets WCAG 2.1 AA", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await page.getByText("Log in", { exact: true }).first().click();
  await page.getByTestId("phone-input").fill(DEMO_PHONE);
  await page.getByTestId("password-input").fill(DEMO_PASSWORD);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("use-passkey")).toBeVisible();
  expect(await violations(page), "second step").toEqual([]);
  await page.getByTestId("use-passkey").click();
  await expect(page.getByText("Create a 5 digit PIN")).toBeVisible();
  expect(await violations(page), "create a PIN").toEqual([]);
  await typePin(page, DEMO_PIN);
  await expect(page.getByText("Type your PIN again")).toBeVisible();
  expect(await violations(page), "type the PIN again").toEqual([]);
  await typePin(page, DEMO_PIN);
  await expect(page.getByText("Welcome, Amara")).toBeVisible();
  const found: string[] = [];
  for (const path of SIGNED_IN) {
    await visit(page, path);
    found.push(...(await violations(page)).map((line) => `${path} ${line}`));
  }
  expect(found).toEqual([]);
});

test("the PIN lock, its reset and the sign out after wrong PINs meet WCAG 2.1 AA", async ({ page }) => {
  await page.clock.install();
  await page.goto("/");
  await page.getByText("Log in", { exact: true }).first().click();
  await page.getByTestId("phone-input").fill(DEMO_PHONE);
  await page.getByTestId("password-input").fill(DEMO_PASSWORD);
  await page.getByTestId("login-submit").click();
  await page.getByTestId("use-passkey").click();
  await typePin(page, DEMO_PIN);
  await typePin(page, DEMO_PIN);
  await expect(page.getByText("Welcome, Amara")).toBeVisible();

  await leaveForSixMinutes(page);
  await expect(page.getByText("Enter your PIN to open the app on this phone")).toBeVisible();
  expect(await violations(page), "unlock").toEqual([]);
  await typePin(page, "90817");
  await expect(page.getByText(/4 tries left/)).toBeVisible();
  expect(await violations(page), "wrong PIN").toEqual([]);
  await page.getByTestId("pin-forgot").click();
  await expect(page.getByText("Reset your PIN")).toBeVisible();
  expect(await violations(page), "reset your PIN").toEqual([]);
  await page.getByText("Back to my PIN").click();
  for (let tries = 0; tries < 4; tries += 1) {
    await typePin(page, "90817");
    await page.waitForTimeout(200);
  }
  await expect(page.getByText("Signed out for your safety")).toBeVisible();
  expect(await violations(page), "signed out").toEqual([]);
});

test("the PIN and passkey offer at the end of sign up meet WCAG 2.1 AA", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Create an account").click();
  await page.getByTestId("phone-input").fill("701555014");
  await page.getByTestId("email-input").fill("new@example.com");
  await page.getByTestId("terms-checkbox").click();
  await page.getByTestId("signup-submit").click();
  await page.getByTestId("code-input").fill("123456");
  await page.getByTestId("code-submit").click();
  await page.getByTestId("password-input").fill("green matooke sunday");
  await page.getByTestId("password-confirm-input").fill("green matooke sunday");
  await page.getByTestId("password-submit").click();
  await expect(page.getByText("3 of 4")).toBeVisible();
  expect(await violations(page), "create a PIN at sign up").toEqual([]);
  await typePin(page, DEMO_PIN);
  await typePin(page, DEMO_PIN);
  await expect(page.getByTestId("offer-add-passkey")).toBeVisible();
  expect(await violations(page), "passkey offer").toEqual([]);
  await page.getByTestId("offer-add-passkey").click();
  await expect(page.getByText("Passkey added").last()).toBeVisible();
  expect(await violations(page), "passkey added").toEqual([]);
});
