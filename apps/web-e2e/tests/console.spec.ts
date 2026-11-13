import { expect, test, type Page } from "@playwright/test";

// The console's sample staff (apps/console/src/auth/stub.ts). Any password works there.
const PASSWORD = "not-a-real-password";

/** Go to a screen inside the app, as a link does. Loading the address afresh would sign the
 * sample session out, because the sample keeps nothing between page loads. */
async function visit(page: Page, path: string) {
  await page.evaluate((to) => {
    window.history.pushState({}, "", to);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, path);
}

/** Wait until the console has settled on the role's home screen. Sign-in redirects twice, to
 * "/" and then to the home screen, so a screen visited before the second redirect is undone. */
async function landed(page: Page) {
  await expect(page).not.toHaveURL(/\/sign-in/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

async function signIn(page: Page, email: string) {
  await page.goto("/sign-in");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
}

test("a page asked for before signing in goes to sign-in first", async ({ page }) => {
  await page.goto("/clients");
  await expect(page.getByRole("heading", { name: "Staff sign-in" })).toBeVisible();
  await expect(page).toHaveURL(/\/sign-in/);
  await expect(page.getByText("Sample sign-in. Not connected to Finvest.")).toBeVisible();
});

test("staff sign in with a password and a passkey", async ({ page }) => {
  await signIn(page, "admin@finvest.test");
  await expect(page.getByRole("heading", { name: "Confirm it is you" })).toBeVisible();
  await page.getByRole("button", { name: "Use passkey" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
});

test("staff with only an authenticator app sign in with its code", async ({ page }) => {
  await signIn(page, "finance@finvest.test");
  await expect(page.getByRole("button", { name: /passkey/i })).toHaveCount(0);
  await page.getByLabel("6-digit code").fill("000000");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByLabel("6-digit code").fill("123456");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page).not.toHaveURL(/\/sign-in/);
});

test("staff with no second step set up cannot sign in", async ({ page }) => {
  await signIn(page, "invited@finvest.test");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toHaveCount(0);
});

test("typing the address of a screen the role may not open shows no access", async ({
  page,
}) => {
  await signIn(page, "support@finvest.test");
  await page.getByRole("button", { name: "Use passkey" }).click();
  await landed(page);
  await visit(page, "/dealing-desk");
  await expect(page).toHaveURL(/\/dealing-desk/);
  await expect(page.getByRole("heading", { name: "You cannot open this screen" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare an order" })).toHaveCount(0);
});

test("on the dealing desk an order is approved by a second person, never its preparer", async ({
  page,
}) => {
  await signIn(page, "admin@finvest.test");
  await page.getByRole("button", { name: "Use passkey" }).click();
  await landed(page);
  await visit(page, "/dealing-desk");
  await expect(page).toHaveURL(/\/dealing-desk/);
  await page.getByRole("button", { name: "Prepare an order" }).click();
  const form = page.getByRole("form", { name: "Prepare an order" });
  await form.getByLabel("What is it?").selectOption("Secondary market sale");
  await form.getByLabel("Face value (UGX)").fill("50,000,000");
  await form.getByRole("button", { name: "Send for approval" }).click();
  await expect(
    page.getByText(/You prepared this order, so someone else has to approve it/),
  ).toBeVisible();
  const approve = page.getByRole("button", { name: "Approve as second person" });
  // The sample desk holds other people's orders, which this person may approve; their own
  // order offers only to withdraw it.
  await expect(page.getByRole("button", { name: "Withdraw this order" })).toBeVisible();
  await approve.first().click();
  await expect(page.getByText("Approved, waiting for the result").first()).toBeVisible();
});
