import { ensureClientSignedIn } from "../lib/client.mjs";

const tid = (page, id) => page.getByTestId(id).locator("visible=true");
const tab = (page) => page.locator("[aria-label=Profile]").locator("visible=true");

/** Back to Profile: by the tab when the screen shows it, otherwise by Back as she would. */
async function toProfile(t, page) {
  for (let i = 0; i < 4 && !(await tab(page).count()); i++) {
    await t.click(page.getByRole("button", { name: "Back" }).locator("visible=true"));
  }
  await t.click(tab(page).last());
}

export default {
  id: "16",
  title: "Making it hers",
  subtitle: "Her answers, her people, her alerts and her data.",
  steps: [
    { caption: "Four questions help Finvest suggest what suits her", does: "Profile, About your investing, four answers, the result" },
    { caption: "She names her brother as next of kin", does: "Next of kin, Add a person, Moses, Brother or sister, his number and ID" },
    { caption: "A copy of everything Finvest holds is one tap away", does: "Language and data, Download a copy of my data" },
    { caption: "She picks her Airtel number for the school fees money", does: "Portfolio, the bill, Change what happens, Pay out, Airtel, Save" },
    { caption: "She chooses which alerts also come by SMS", does: "Notifications, Settings, Investments by SMS" },
    { caption: "Devices shows everywhere she is signed in", does: "Sign in and security, Devices" },
    { caption: "If her phone is lost, she can freeze the account", does: "Freeze my account, read, Not now; Close my account, read, back" },
  ],
  takes: [{ name: "profile", who: "esther-phone", layout: "phone" }],
  async profile(t, page) {
    // Downloading her data needs a recent log in, so start this take from a fresh one.
    await page.context().clearCookies();
    await ensureClientSignedIn(page, "esther", "/");
    await t.start();
    await t.step("Four questions help Finvest suggest what suits her", { hold: 800 });
    await toProfile(t, page);
    await t.click(tid(page, "profile-investing-questions"));
    await t.read(1400);
    for (const answer of [
      "risk-purpose-planned_purchase",
      "risk-need_back-3_to_12_months",
      "risk-early_need-would_wait",
      "risk-experience-savings_or_sacco",
    ]) {
      await t.click(tid(page, answer));
      await t.read(600);
      await t.click(tid(page, "risk-next"));
    }
    await tid(page, "risk-result").waitFor();
    await t.zoom(tid(page, "risk-result"), { hold: 2400 });
    await t.click(tid(page, "risk-done"));

    await t.step("She names her brother as next of kin", { hold: 300 });
    await toProfile(t, page);
    await t.click(tid(page, "profile-next-of-kin"));
    await t.read(1400);
    await t.click(tid(page, "kin-add"));
    await t.type(tid(page, "kin-name"), "Moses Okello");
    await t.click(tid(page, "kin-relationship-sibling"));
    await t.type(tid(page, "phone-input"), "700000736");
    await t.type(tid(page, "kin-id"), "CM85092A7LPQ3D");
    await t.click(tid(page, "kin-save"));
    await page.getByText("Moses Okello is on your list.").waitFor();
    await t.read(1800);

    await t.step("A copy of everything Finvest holds is one tap away", { hold: 300 });
    await toProfile(t, page);
    await t.click(tid(page, "profile-language"));
    await t.read(1600);
    const download = page.waitForEvent("download", { timeout: 30000 });
    await t.click(tid(page, "language-download"));
    const file = await download;
    if (!file.suggestedFilename().startsWith("finvest-my-data-")) throw new Error("No copy of her data");
    await page.getByText("Your copy is saved").waitFor();
    await t.zoom(page.getByText("Your copy is saved").locator("xpath=.."), { hold: 2000 });

    await t.step("She picks her Airtel number for the school fees money", { hold: 300 });
    await t.cut(() => page.goto("https://app.finvestug.com/portfolio"));
    await t.click(page.getByRole("button", { name: /worth UGX 90/ }).first());
    await t.click(tid(page, "change-maturity"));
    await t.click(tid(page, "maturity-pay_out"));
    await t.click(page.locator("[data-testid^=account-]").locator("visible=true").first());
    await t.read(1600);
    await t.click(tid(page, "maturity-save"));
    await tid(page, "term-progress").waitFor();
    await t.read(1800);

    await t.step("She chooses which alerts also come by SMS", { hold: 300 });
    await toProfile(t, page);
    await t.click(tid(page, "profile-notifications"));
    await t.read(1600);
    await t.click(page.getByText("Settings", { exact: true }).locator("visible=true"));
    await tid(page, "pref-investments-sms").waitFor();
    await t.read(1400);
    if ((await tid(page, "pref-investments-sms").getAttribute("aria-checked")) !== "true") {
      await t.click(tid(page, "pref-investments-sms"));
    }
    await t.read(1600);

    await t.step("Devices shows everywhere she is signed in", { hold: 300 });
    await toProfile(t, page);
    await t.click(tid(page, "open-security"));
    await t.read(1600);
    await t.click(tid(page, "open-devices"));
    await page.getByText("This device").first().waitFor();
    await t.read(2400);

    await t.step("If her phone is lost, she can freeze the account", { hold: 300 });
    await toProfile(t, page);
    await t.click(tid(page, "profile-freeze"));
    await tid(page, "freeze-cancel").waitFor();
    await t.read(3400);
    await t.click(tid(page, "freeze-cancel"));
    await t.click(tid(page, "profile-close"));
    await tid(page, "close-start").waitFor();
    await t.read(3000);
    await t.click(page.getByRole("button", { name: "Back" }).locator("visible=true"));
    await t.read(800);
    await t.stop();
  },
};
