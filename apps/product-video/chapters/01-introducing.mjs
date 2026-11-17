import { CLIENT } from "../lib/people.mjs";
import { lastSms, nextSms } from "../lib/session.mjs";

export default {
  id: "01",
  kicker: "Introducing",
  title: "Finvest",
  subtitle: "Treasury bills and bonds, from your phone.",
  steps: [
    { caption: "Finvest puts Uganda's investments on your phone", does: "finvestug.com home page, cursor rests on the rate pill (zoom)" },
    { caption: "Every product shows its latest rate, after tax", does: "scroll to the rates, zoom on the product cards" },
    { caption: "Bills and bonds are open, with more on the way", does: "scroll to One app for the investments Uganda trusts (zoom)" },
    { caption: "Work out what you could earn before you pay", does: "scroll to the calculator, type 1,000,000" },
    { caption: "Esther, a Kampala teacher, signs up on her phone", does: "app.finvestug.com welcome screen on the phone, tap Create an account" },
    { caption: "Her mobile money number becomes her login", does: `type ${CLIENT.phoneShown} and ${CLIENT.email}` },
    { caption: "She agrees to the terms and asks for a code", does: "tick the terms, tap Text me a code" },
    { caption: "A texted code proves the phone is hers", does: "the real text message arrives, type its code, Continue" },
    { caption: "A strong password protects the account", does: "type the password twice into masked fields, Continue" },
    { caption: "Next, she confirms her email address", does: "the Check your email step (3 of 4)" },
  ],
  takes: [
    { name: "site", who: "visitor", layout: "wide" },
    { name: "signup", who: "esther-phone", layout: "phone" },
  ],
  async site(t, page) {
    await page.goto("https://finvestug.com/");
    await page.waitForLoadState("networkidle");
    await t.start();
    await t.step("Finvest puts Uganda's investments on your phone", { hold: 1200 });
    await t.hover(page.locator("main").getByText(/91 day treasury bills at/).first());
    await t.zoom(page.locator("main").getByText(/91 day treasury bills at/).first(), { hold: 1600 });
    await t.step("Every product shows its latest rate, after tax", { hold: 600 });
    await t.scroll(780, { ms: 1400 });
    await t.read(1200);
    await t.zoom(page.locator("#rates [data-product-grid]"), { hold: 2400, scale: 1.1 });
    await t.step("Bills and bonds are open, with more on the way", { hold: 400 });
    await page.locator("#range").scrollIntoViewIfNeeded();
    await t.read(1600);
    await t.zoom(page.locator("#range"), { hold: 2400, scale: 1.05 });
    await t.step("Work out what you could earn before you pay", { hold: 500 });
    await page.locator("#calculator").scrollIntoViewIfNeeded();
    await t.wait(900);
    await t.type("#calc-amount", "1000000");
    await t.read(1500);
    await t.zoom("#calc-note", { hold: 1800 });
    await t.stop();
  },
  async signup(t, page) {
    await page.goto("https://app.finvestug.com/");
    await page.getByText("Create an account").first().waitFor();
    await t.start();
    await t.step("Esther, a Kampala teacher, signs up on her phone", { hold: 1500 });
    await t.click(page.getByText("Create an account").first());
    await t.step("Her mobile money number becomes her login", { hold: 900 });
    await t.type(page.getByTestId("phone-input"), CLIENT.phone.slice(1));
    await t.type(page.getByTestId("email-input"), CLIENT.email);
    await t.step("She agrees to the terms and asks for a code", { hold: 700 });
    await t.click(page.getByTestId("terms-checkbox"), { dx: 0.08 });
    const before = lastSms(CLIENT.phone);
    await t.click(page.getByTestId("signup-submit"));
    const sms = await t.cut(() => nextSms(CLIENT.phone, before));
    await page.getByTestId("code-input").locator("visible=true").waitFor();
    await t.step("A texted code proves the phone is hers", { hold: 400 });
    await t.sms(sms.text);
    await t.wait(1200);
    await t.type(page.getByTestId("code-input").locator("visible=true"), sms.code, { delay: 110 });
    await t.click(page.getByTestId("code-submit").locator("visible=true"));
    await t.step("A strong password protects the account", { hold: 700 });
    await t.type(page.getByTestId("password-input").locator("visible=true"), CLIENT.password);
    await t.type(page.getByTestId("password-confirm-input").locator("visible=true"), CLIENT.password);
    await t.click(page.getByTestId("password-submit").locator("visible=true"));
    await page.getByText("Check your email").first().waitFor();
    await t.step("Next, she confirms her email address", { hold: 800 });
    await t.read(2200);
    await t.stop();
  },
};
