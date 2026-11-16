const tid = (page, id) => page.getByTestId(id).locator("visible=true");

export default {
  id: "08",
  title: "Saving for school fees",
  subtitle: "A goal with a target, a date and a payday.",
  steps: [
    { caption: "Goals turn a big cost into monthly savings", does: "Goals tab, Start your first goal" },
    { caption: "She names it for her son, Daniel", does: "School fees, name: Daniel's school fees" },
    { caption: "Finvest works out what to save each month", does: "UGX 3,000,000 in 1 year; the plan (zoom)" },
    { caption: "Every saving goes into a Treasury bill", does: "read How your money will work, Next" },
    { caption: "Her first saving goes in by mobile money", does: "first saving UGX 300,000, Create goal and pay, Airtel Money, Pay" },
    { caption: "A payday reminder on the 25th keeps her on track", does: "open the goal, Payday reminder, the 25th, Save" },
    { caption: "Locking the goal keeps hands off the money", does: "Lock this goal, confirm" },
  ],
  takes: [{ name: "goal", who: "esther-phone", layout: "phone" }],
  async goal(t, page) {
    await page.goto("https://app.finvestug.com/goals");
    await tid(page, "first-goal").waitFor();
    await t.start();
    await t.step("Goals turn a big cost into monthly savings", { hold: 1800 });
    await t.click(tid(page, "first-goal"));
    await t.step("She names it for her son, Daniel", { hold: 500 });
    await t.click(tid(page, "category-school_fees"));
    await t.type(tid(page, "goal-name"), "Daniel's school fees");
    await t.click(tid(page, "goal-continue"));
    await t.step("Finvest works out what to save each month", { hold: 500 });
    await t.type(tid(page, "goal-target"), "3000000");
    await t.click(tid(page, "horizon-12"));
    await page.getByText("To get there, save").waitFor();
    await t.zoom(page.getByText("To get there, save").locator("xpath=.."), { hold: 2600 });
    await t.step("Every saving goes into a Treasury bill", { hold: 400 });
    await t.scroll(260, { ms: 800 });
    await t.read(2400);
    await t.click(tid(page, "plan-next"));
    await t.step("Her first saving goes in by mobile money", { hold: 900 });
    await t.type(tid(page, "first-saving"), "300000");
    await t.click(tid(page, "create-and-save"));
    await t.click(tid(page, "network-airtel"));
    await t.click(tid(page, "pay-submit"));
    await page.getByText("Approve on your phone").waitFor();
    await t.read(1200);
    await t.cut(() => tid(page, "receipt-done").waitFor({ timeout: 90000 }));
    await t.read(1800);
    await t.click(tid(page, "receipt-done"));
    await t.step("A payday reminder on the 25th keeps her on track", { hold: 400 });
    // Done after the first saving goes back to the goal.
    await tid(page, "goal-payday").waitFor();
    await t.read(1600);
    await t.click(tid(page, "goal-payday"));
    await t.click(tid(page, "payday-25"));
    await t.click(tid(page, "payday-save"));
    await page.getByText(/on your payday, 25 Nov/).first().waitFor();
    await t.zoom(page.getByText(/on your payday, 25 Nov/).first(), { hold: 2000 });
    await t.step("Locking the goal keeps hands off the money", { hold: 400 });
    await t.click(tid(page, "goal-lock"));
    await t.read(1500);
    await t.click(tid(page, "lock-yes"));
    await t.wait(1800);
    await t.read(2200);
    await t.stop();
  },
};
