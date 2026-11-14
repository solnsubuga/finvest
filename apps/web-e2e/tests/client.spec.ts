import { expect, test, type Page } from "@playwright/test";

import { createPin, DEMO_PIN, leaveForSixMinutes, typePin } from "./pin";

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

  await expect(page.getByText("3 of 4")).toBeVisible();
  await createPin(page);
  // The browser can make a passkey, so sign up ends by offering one.
  await expect(page.getByText("4 of 4")).toBeVisible();
  await id(page, "offer-not-now").click();

  await expect(page.getByTestId("home")).toBeVisible();
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
  await expect(page.getByTestId("home")).toHaveCount(0);
  // A wrong code clears the field for the next try.
  await expect(id(page, "code-input")).toHaveValue("");
  await id(page, "code-input").fill(DEMO_CODE);
  await id(page, "code-submit").click();
  await createPin(page);
  await expect(page.getByTestId("home")).toBeVisible();
});

test("a client pays for an investment by mobile money from their own number", async ({
  page,
}) => {
  await logIn(page);
  await id(page, "use-passkey").click();
  await createPin(page);
  await expect(page.getByTestId("home")).toBeVisible();
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
  await createPin(page);
  await expect(page.getByTestId("home")).toBeVisible();
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

  // On a phone with a PIN (and in the demo), adding a payout account is confirmed with it.
  await expect(page.getByText("Enter your PIN to add this payout account")).toBeVisible();
  await typePin(page, "90817");
  await expect(page.getByText("That PIN is not right. 4 tries left before this phone is signed out.")).toBeVisible();
  await typePin(page, DEMO_PIN);

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

test("a PIN that is too simple is refused, and two that differ start again", async ({ page }) => {
  await logIn(page);
  await id(page, "use-passkey").click();
  await expect(page.getByText("Create a 5 digit PIN")).toBeVisible();
  await typePin(page, "12345");
  await expect(page.getByText(/That PIN is too easy to guess/)).toBeVisible();
  await typePin(page, DEMO_PIN);
  await typePin(page, "48152");
  await expect(page.getByText("Those PINs did not match. Choose your PIN again.")).toBeVisible();
  await createPin(page);
  await expect(page.getByTestId("home")).toBeVisible();
});

test("the app locks in the background and opens with the PIN, after a wrong one", async ({ page }) => {
  await page.clock.install();
  await logIn(page);
  await id(page, "use-passkey").click();
  await createPin(page);
  await expect(page.getByTestId("home")).toBeVisible();

  await leaveForSixMinutes(page);
  await expect(page.getByText("Enter your PIN to open the app on this phone")).toBeVisible();
  await expect(page.getByTestId("home")).toHaveCount(0);
  await typePin(page, "90817");
  await expect(
    page.getByText("That PIN is not right. 4 tries left before this phone is signed out."),
  ).toBeVisible();
  await typePin(page, DEMO_PIN);
  await expect(page.getByTestId("home")).toBeVisible();
});

test("five wrong PINs sign the phone out, and the client logs in again", async ({ page }) => {
  await page.clock.install();
  await logIn(page);
  await id(page, "use-passkey").click();
  await createPin(page);
  await expect(page.getByTestId("home")).toBeVisible();

  await leaveForSixMinutes(page);
  for (let tries = 5; tries > 1; tries -= 1) {
    await typePin(page, "90817");
    await expect(page.getByText(new RegExp(`${tries - 1} tr(y|ies) left`))).toBeVisible();
  }
  await typePin(page, "90817");
  await expect(page.getByText("Signed out for your safety")).toBeVisible();
  await id(page, "pin-log-in-again").click();
  await expect(page.getByText("Welcome back")).toBeVisible();
});

test("a client who lost their phone asks to be called back", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Log in", { exact: true }).first().click();
  await page.getByText("I changed my number or cannot get in").click();
  await expect(page.getByText("Get back into your account").last()).toBeVisible();
  await id(page, "recover-phone").click();

  await shown(page, "call-back-submit").click();
  await expect(page.getByText("Enter the number on your account, like 772 123 456.")).toBeVisible();
  await shown(page, "reason-new_number").click();
  await shown(page, "account-phone-input").fill(DEMO_PHONE);
  await shown(page, "contact-phone-input").fill("701555013");
  await shown(page, "call-back-submit").click();

  await expect(page.getByText("We will call you").last()).toBeVisible();
  await expect(page.getByText(/If that number has a Finvest account/)).toBeVisible();
});

// A 1 by 1 pixel PNG: the browser reads it as a picture, and the demo takes any photo.
const PHOTO = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);

/** The step on top. Earlier steps stay in the page, hidden, while the stack holds them. */
const shown = (page: Page, testId: string) => page.getByTestId(testId).filter({ visible: true });

async function photograph(page: Page, name: string) {
  const chooser = page.waitForEvent("filechooser");
  await shown(page, "photo-take").click();
  await (await chooser).setFiles({ name, mimeType: "image/png", buffer: PHOTO });
  await expect(shown(page, "photo-preview")).toBeVisible();
  await shown(page, "photo-use").click();
}

/** Signs in an account with no ID check yet and fills in every step before the selfie. */
async function upToTheSelfie(page: Page) {
  // An account with no ID check yet, signed in with an authenticator code.
  await logIn(page, "701000200");
  await id(page, "use-authenticator").click();
  await id(page, "code-input").fill(DEMO_CODE);
  await id(page, "code-submit").click();
  await createPin(page);

  await id(page, "set-up-id").click();
  await expect(page.getByText("Which ID will you use?")).toBeVisible();
  await shown(page, "document-continue").click();

  await expect(page.getByText("Front of your National ID")).toBeVisible();
  await photograph(page, "front.png");
  await expect(page.getByText("Back of your National ID")).toBeVisible();
  await photograph(page, "back.png");

  await shown(page, "details-surname").fill("OKELLO");
  await shown(page, "details-given-names").fill("Peter");
  await shown(page, "details-number").fill("CF94012345");
  await shown(page, "details-born").fill("01/02/1990");
  await shown(page, "details-expires").fill("18/09/2030");
  await shown(page, "details-save").click();

  await expect(page.getByText("5 of 5")).toBeVisible();
}

test("a new client hands in their ID, details and a live selfie for checking", async ({ page }) => {
  await upToTheSelfie(page);

  // The selfie is a live check on the partner's page. The demo page is a stand-in, so its
  // tab is closed again, and the demo check passes after a couple of reads.
  await expect(shown(page, "live-start")).toBeVisible();
  await shown(page, "live-start").click();
  await expect(page.getByText("Your live check is ready").filter({ visible: true })).toBeVisible();
  const popup = page.waitForEvent("popup");
  await shown(page, "live-open").click();
  await (await popup).close();
  await expect(page.getByText("Finish the check, then come back").filter({ visible: true })).toBeVisible();
  await expect(async () => {
    await shown(page, "live-finished").click();
    await expect(page.getByText("Live selfie done").filter({ visible: true })).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: 20_000 });
  await shown(page, "live-continue").click();

  await expect(page.getByText("Thanks, Peter. We are checking now.")).toBeVisible();
  await shown(page, "id-check-home").click();
  await expect(page.getByText("We are checking your ID")).toBeVisible();
});

test("a client who cannot take the live check hands in a camera selfie instead", async ({ page }) => {
  await upToTheSelfie(page);

  await shown(page, "live-camera").click();
  await photograph(page, "selfie.png");

  await expect(page.getByText("Thanks, Peter. We are checking now.")).toBeVisible();
});

test("a client sends part of an investment to another client after checking the details", async ({ page }) => {
  await logIn(page);
  await id(page, "use-passkey").click();
  await createPin(page);
  await expect(page.getByTestId("home")).toBeVisible();
  // Portfolio opens from Profile; the app's router keeps the sample session.
  await page.evaluate(() => {
    window.history.pushState({}, "", "/portfolio");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await openToSend(page);

  await id(page, "send-to").fill("0772 555 170");
  await id(page, "find-recipient").click();
  await expect(id(page, "recipient")).toContainText("Brian K.");
  await expect(id(page, "recipient")).toContainText("Finvest account 1001005170");
  await id(page, "send-amount").fill("1000000");
  await id(page, "send-purpose-gift").click();
  await id(page, "review-send").click();
  await expect(id(page, "send-confirm")).toContainText("You are sending UGX 1,000,000 to Brian K.");
  await id(page, "confirm-send").click();

  await expect(page.getByText("To Brian K.")).toBeVisible();
  await expect(id(page, "transfer-amount")).toHaveText("UGX 1,000,000");
  await id(page, "open-kept").click();
  // The new investment holds what was kept. Screens left behind in the stack stay hidden.
  await expect(page.locator("text=You invested >> visible=true")).toBeVisible();
});

/** A phone opens the investment's own screen to send; a wide Portfolio has Send in the detail beside its table. */
async function openToSend(page: Page) {
  const card = page.getByLabel(/^91 Day Treasury Bill, Running/).first();
  const row = page.getByLabel(/^91 Day Treasury Bill, worth/).first();
  await card.or(row).first().waitFor();
  if (await card.count()) {
    await card.click();
    await page.getByTestId("send-investment").click();
  } else {
    await row.click();
    await page.getByTestId("detail-send").click();
  }
}
