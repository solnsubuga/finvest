import { expect, test, type Page } from "@playwright/test";

// The client app's sample sign-in (apps/client/src/auth/stubAuthClient.ts). Demo values, not
// credentials for any real system.
const DEMO_PHONE = "772123456";
const DEMO_PASSWORD = "correcthorse1";
const DEMO_CODE = "123456";

const id = (page: Page, testId: string) => page.getByTestId(testId);

async function logIn(page: Page, phone = DEMO_PHONE) {
  await page.goto("/");
  await page.getByText("Log in", { exact: true }).first().click();
  await id(page, "phone-input").fill(phone);
  await id(page, "password-input").fill(DEMO_PASSWORD);
  await id(page, "login-submit").click();
}

test("the welcome screen says the sign-in is a demo", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Create an account")).toBeVisible();
  await expect(page.getByText(/Demo sign-in, not connected to Finvest yet/)).toBeVisible();
});

test("a new client signs up with a texted code and a password", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Create an account").click();
  await id(page, "signup-submit").click();
  await expect(page.getByText("Enter a mobile number like 772 123 456.")).toBeVisible();

  await id(page, "phone-input").fill("701555012");
  await id(page, "email-input").fill("new@example.com");
  await id(page, "terms-checkbox").click();
  await id(page, "signup-submit").click();
  await id(page, "code-input").fill(DEMO_CODE);
  await id(page, "code-submit").click();
  await id(page, "password-input").fill("green matooke sunday");
  await id(page, "password-confirm-input").fill("green matooke sunday");
  await id(page, "password-submit").click();

  await expect(page.getByText("Welcome to Finvest")).toBeVisible();
});

test("a wrong password does not sign in", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Log in", { exact: true }).first().click();
  await id(page, "phone-input").fill(DEMO_PHONE);
  await id(page, "password-input").fill("not the password");
  await id(page, "login-submit").click();
  await expect(page.getByText("Welcome back")).toBeVisible();
  await expect(id(page, "use-authenticator")).toHaveCount(0);
});

test("a client signs in with the password and an authenticator code", async ({ page }) => {
  await logIn(page);
  await id(page, "use-authenticator").click();
  await id(page, "code-input").fill("000000");
  await id(page, "code-submit").click();
  await expect(page.getByText("That code is not right. Check it and try again.")).toBeVisible();
  await expect(page.getByText("Welcome, Amara")).toHaveCount(0);
  // A wrong code clears the field for the next try.
  await expect(id(page, "code-input")).toHaveValue("");
  await id(page, "code-input").fill(DEMO_CODE);
  await id(page, "code-submit").click();
  await expect(page.getByText("Welcome, Amara")).toBeVisible();
});

test("a client pays for an investment by mobile money from their own number", async ({
  page,
}) => {
  await logIn(page);
  await id(page, "use-passkey").click();
  await expect(page.getByText("Welcome, Amara")).toBeVisible();
  // The phone's tab bar and the wide sidebar both name it Invest.
  await page
    .getByRole("link", { name: "Invest", exact: true })
    .or(page.getByLabel("Invest", { exact: true }))
    .first()
    .click();
  await id(page, "demo-pay").click();

  await expect(page.getByText("Pay UGX 1,000,000")).toBeVisible();
  await expect(page.getByText("+256 772 123 456")).toBeVisible();
  // The number is the account's own: there is nothing to type it into.
  await expect(id(page, "phone-input")).toHaveCount(0);
  await id(page, "pay-submit").click();

  await expect(page.getByText("Approve on your phone")).toBeVisible();
  await expect(page.getByText("Completed")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Paid into 91 Day Treasury Bill")).toBeVisible();
});

test("a client adds a mobile money number to be paid to, and enters the texted code", async ({ page }) => {
  await logIn(page);
  await id(page, "use-passkey").click();
  await expect(page.getByText("Welcome, Amara")).toBeVisible();
  // The phone's tab bar and the wide sidebar both name it Profile.
  await page
    .getByRole("link", { name: "Profile", exact: true })
    .or(page.getByLabel("Profile", { exact: true }))
    .first()
    .click();
  await id(page, "open-payout-accounts").click();
  await expect(id(page, "payout-account-demo-account-mtn")).toBeVisible();

  await id(page, "payout-account-add").click();
  // The client's own number is filled in; this one is another of theirs.
  await id(page, "phone-input").fill("701 555 012");
  await id(page, "holder-name-input").fill("Amara Nakato");
  await id(page, "payout-account-save").click();

  await expect(page.getByText(/We texted a code to \+256 70• ••• 012/)).toBeVisible();
  await id(page, "code-input").fill("000000");
  await id(page, "payout-code-submit").click();
  await expect(page.getByText("That code is not right. Check it and try again.")).toBeVisible();
  await id(page, "code-input").fill(DEMO_CODE);
  await id(page, "payout-code-submit").click();

  // A mobile money number is checked against the name the network holds, not by proof.
  await expect(page.getByText(/We are checking the name the network holds for this number/)).toBeVisible();
  await expect(id(page, "proof-upload")).toHaveCount(0);
});
