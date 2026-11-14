import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Every screen of the console, checked against WCAG 2.1 A and AA with axe, wide and on a phone.
// The console runs on its sample sign-in (apps/console/src/auth/stub.ts); any password works.
const PASSWORD = "not-a-real-password";

const SCREENS = [
  "/overview",
  "/review-queue",
  "/identity-checks",
  "/maturities",
  "/support",
  "/complaints",
  "/clients",
  "/money-movement",
  "/reconciliation",
  "/investments",
  "/automatic-investing",
  "/goals",
  "/dealing-desk",
  "/finance",
  "/products",
  "/rates",
  "/compliance",
  "/team",
  "/reports",
  "/settings",
  "/migration",
];

async function violations(page: Page): Promise<string[]> {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  return result.violations.flatMap((rule) =>
    rule.nodes.map((node) => `${rule.id}: ${node.target.join(" ")}`),
  );
}

/** Go to a screen as a link does. Loading the address afresh would sign the sample out. */
async function visit(page: Page, path: string) {
  await page.evaluate((to) => {
    window.history.pushState({}, "", to);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.waitForLoadState("networkidle");
}

test("sign-in meets WCAG 2.1 AA", async ({ page }) => {
  await page.goto("/sign-in");
  await expect(page.getByRole("heading", { name: "Staff sign-in" })).toBeVisible();
  expect(await violations(page), "sign-in").toEqual([]);
  await page.getByLabel("Work email").fill("admin@finvest.test");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Confirm it is you" })).toBeVisible();
  expect(await violations(page), "second step").toEqual([]);
});

test("every console screen meets WCAG 2.1 AA", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/sign-in");
  await page.getByLabel("Work email").fill("admin@finvest.test");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Use passkey" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  const found: string[] = [];
  for (const path of SCREENS) {
    await visit(page, path);
    found.push(...(await violations(page)).map((line) => `${path} ${line}`));
  }
  expect(found).toEqual([]);
});
