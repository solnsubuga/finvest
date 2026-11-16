import { CLIENT } from "../lib/people.mjs";
import { lastSms, nextSms } from "../lib/session.mjs";

const tid = (page, id) => page.getByTestId(id).locator("visible=true");
const side = (page, name) => page.locator("[data-testid^=sidebar-group]").getByText(name, { exact: true });

export default {
  id: "13",
  title: "The same account on a laptop",
  subtitle: "Everything from the phone, on a bigger screen.",
  steps: [
    { caption: "On her laptop, Esther logs in with her number and password", does: "app.finvestug.com: Log in, number, masked password, Continue" },
    { caption: "A texted code confirms it is her", does: "Send me a code, type the code from the SMS" },
    { caption: "Home shows what she has and what it earns", does: "read Home" },
    { caption: "Portfolio shows each investment beside its details", does: "Portfolio, pick the UGX 1,000,000 bill" },
    { caption: "Insights show what she earned and what comes next", does: "Insights" },
    { caption: "Activity keeps every payment, ready to download", does: "Activity, open a row" },
    { caption: "She adds a passkey for the laptop, for next time", does: "Profile, Sign in and security, Passkey, Add a passkey on this device" },
  ],
  takes: [{ name: "laptop", who: "esther-wide", layout: "wide" }],
  async laptop(t, page) {
    await page.goto("https://app.finvestug.com/");
    await page.getByRole("button", { name: "Log in" }).waitFor();
    await t.start();
    await t.step("On her laptop, Esther logs in with her number and password", { hold: 1200 });
    await t.click(page.getByRole("button", { name: "Log in" }));
    await t.type(tid(page, "phone-input"), CLIENT.phone.slice(1));
    await t.type(tid(page, "password-input"), CLIENT.password);
    await t.click(tid(page, "login-submit"));
    await tid(page, "use-code").waitFor();
    await t.step("A texted code confirms it is her", { hold: 900 });
    const before = lastSms(CLIENT.phone);
    await t.click(tid(page, "use-code"));
    const sms = await t.cut(() => nextSms(CLIENT.phone, before));
    await tid(page, "code-input").waitFor();
    await t.read(600);
    await t.type(tid(page, "code-input"), sms.code, { delay: 110 });
    await t.click(tid(page, "code-submit"));
    await tid(page, "home-total").waitFor();
    await t.step("Home shows what she has and what it earns", { hold: 1600 });
    await t.zoom(tid(page, "home-total"), { hold: 2000 });
    await t.read(1400);
    await t.step("Portfolio shows each investment beside its details", { hold: 300 });
    await t.click(side(page, "Portfolio"));
    await page.getByText("Where it is invested").waitFor();
    await t.read(1600);
    await t.click(page.getByText("1,000,000", { exact: true }).locator("visible=true").first());
    await t.read(2600);
    await t.step("Insights show what she earned and what comes next", { hold: 300 });
    await t.click(side(page, "Insights"));
    await page.getByText("COMING UP").or(page.getByText("Coming up")).first().waitFor();
    await t.read(3600);
    await t.step("Activity keeps every payment, ready to download", { hold: 300 });
    await t.click(side(page, "Activity"));
    await tid(page, "activity-csv").waitFor();
    await t.read(1600);
    await t.click(page.getByText(/Payment for 91 Day Treasury Bill/).locator("visible=true").first());
    await t.read(2600);
    await t.step("She adds a passkey for the laptop, for next time", { hold: 300 });
    await t.click(page.getByTestId("sidebar-profile"));
    await page.getByText("Your details, how you sign in").waitFor();
    await t.read(1200);
    await t.click(tid(page, "open-security"));
    await tid(page, "on-passkey").waitFor();
    await t.read(1200);
    await t.click(tid(page, "on-passkey"));
    await t.click(tid(page, "add-passkey"));
    await t.read(1600);
    await t.click(tid(page, "add-passkey"));
    await tid(page, "done").waitFor();
    await t.read(2400);
    await t.stop();
  },
};
