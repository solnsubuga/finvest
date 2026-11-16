const tid = (page, id) => page.getByTestId(id).locator("visible=true");

export default {
  id: "09",
  title: "Investing on autopilot",
  subtitle: "The same amount, every payday, without lifting a finger.",
  steps: [
    { caption: "Auto-invest puts money to work on a schedule", does: "Home, Invest automatically" },
    { caption: "She picks the 182 day bill and an amount", does: "182 Day Treasury Bill, UGX 250,000" },
    { caption: "Every month on the 25th, her payday", does: "Every month, the 25th" },
    { caption: "Each bill rolls over when it matures", does: "Roll it over, Airtel Money, read the summary" },
    { caption: "Nothing is taken until she approves each prompt", does: "Start plan, the plan's page" },
  ],
  takes: [{ name: "plan", who: "esther-phone", layout: "phone" }],
  async plan(t, page) {
    await page.goto("https://app.finvestug.com/");
    await page.getByTestId("home-autoinvest").locator("visible=true").waitFor();
    await t.start();
    await t.step("Auto-invest puts money to work on a schedule", { hold: 1200 });
    await t.click(tid(page, "home-autoinvest"));
    const start = page.getByRole("button", { name: /Start a plan|New plan|Set up/ }).locator("visible=true");
    if (!(await tid(page, "plan-amount").isVisible().catch(() => false)) && (await start.count())) await t.click(start.first());
    await tid(page, "plan-amount").waitFor();
    await t.step("She picks the 182 day bill and an amount", { hold: 500 });
    await t.click(tid(page, "product-TREA-182"));
    await t.type(tid(page, "plan-amount"), "250000");
    await t.step("Every month on the 25th, her payday", { hold: 500 });
    await t.click(tid(page, "frequency-monthly"));
    await t.click(tid(page, "day-25"));
    await t.step("Each bill rolls over when it matures", { hold: 500 });
    await t.click(tid(page, "maturity-roll_over"));
    await t.click(tid(page, "network-airtel"));
    await t.zoom(tid(page, "plan-summary"), { hold: 2600 });
    await t.step("Nothing is taken until she approves each prompt", { hold: 500 });
    await t.click(tid(page, "plan-start"));
    await tid(page, "plan-next").waitFor();
    await t.read(3200);
    await t.stop();
  },
};
