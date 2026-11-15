export default {
  id: "02",
  title: "Make it yours, and safe",
  subtitle: "A passkey for signing in, and a confirmed email.",
  steps: [
    { caption: "Her email will carry statements and receipts", does: "Check your email step, Continue" },
    { caption: "A passkey makes every sign-in quick and safe", does: "read the passkey offer" },
    { caption: "Her phone's screen lock approves the new passkey", does: "Add a passkey (the phone's own authenticator), Passkey added" },
    { caption: "Home shows exactly what to do next", does: "Continue to Home, scroll the set-up list" },
    { caption: "The confirmation email lands in her inbox", does: "test inbox, open Confirm your email address" },
    { caption: "One tap confirms the address is hers", does: "Confirm my email opens app.finvestug.com and confirms it" },
  ],
  takes: [
    { name: "passkey", who: "esther-phone", layout: "phone" },
    { name: "email", who: "inbox", layout: "mail" },
  ],
  async passkey(t, page) {
    await page.goto("https://app.finvestug.com/setup/email");
    await page.getByTestId("email-step-continue").locator("visible=true").waitFor();
    await t.start();
    await t.step("Her email will carry statements and receipts", { hold: 1600 });
    await t.click(page.getByTestId("email-step-continue").locator("visible=true"));
    await page.getByTestId("offer-add-passkey").waitFor();
    await t.step("A passkey makes every sign-in quick and safe", { hold: 1200 });
    await t.read(2400);
    await t.step("Her phone's screen lock approves the new passkey", { hold: 600 });
    await t.click(page.getByTestId("offer-add-passkey"));
    await page.getByText("Passkey added").waitFor();
    await t.read(2200);
    await t.step("Home shows exactly what to do next", { hold: 400 });
    await t.click(page.getByTestId("done").locator("visible=true"));
    await page.getByTestId("home-set-up").waitFor();
    await t.read(1500);
    await t.scroll(420, { ms: 1200 });
    await t.read(1800);
    await t.stop();
  },
  async email(t, page) {
    await page.goto("https://mail.local.test/");
    await t.start();
    await t.step("The confirmation email lands in her inbox", { hold: 900 });
    await t.click(page.locator("nav a", { hasText: "Confirm your email" }));
    await t.read(2000);
    await t.step("One tap confirms the address is hers", { hold: 600 });
    await t.click(page.frameLocator("iframe").getByText("Confirm my email"));
    await page.waitForURL(/app\.finvestug\.com/);
    await page.waitForLoadState("networkidle");
    await t.read(3000);
    await t.stop();
  },
};
