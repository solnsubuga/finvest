const tid = (page, id) => page.getByTestId(id).locator("visible=true");

export default {
  id: "06",
  title: "The first investment",
  subtitle: "From choosing a bill to paying by mobile money.",
  steps: [
    { caption: "Her account is open, so Esther can invest", does: "Home, tap the Invest tab" },
    { caption: "Every bill shows its yearly rate after tax", does: "the Invest list" },
    { caption: "She types an amount and sees what it earns", does: "91 Day Treasury Bill, type 1,000,000, read the return (zoom)" },
    { caption: "At maturity, she wants the money paid out", does: "Review, choose Pay out" },
    { caption: "Card and bank transfer are there too", does: "Confirm and pay, tap Card to see its charge (not paid), see bank transfer" },
    { caption: "Mobile money prompts her own number", does: "Mobile money, Airtel Money, Pay UGX 1,000,000" },
    { caption: "Confirmed in seconds, with a signed receipt", does: "the sandbox payment completes; the receipt" },
    { caption: "Her money earns interest from tonight", does: "Done, Home: total invested and earning a day (zoom)" },
  ],
  takes: [{ name: "pay", who: "esther-phone", layout: "phone" }],
  async pay(t, page) {
    await page.goto("https://app.finvestug.com/");
    await page.getByText(/Good (morning|afternoon|evening)/).first().waitFor();
    await t.start();
    await t.step("Her account is open, so Esther can invest", { hold: 1600 });
    await t.click(page.getByRole("button", { name: "Invest" }).or(page.locator("[aria-label=Invest]")).last());
    await page.getByText("364 Day Treasury Bill").first().waitFor();
    await t.step("Every bill shows its yearly rate after tax", { hold: 1400 });
    await t.read(1600);
    await t.step("She types an amount and sees what it earns", { hold: 400 });
    await t.click(page.getByText("91 Day Treasury Bill").first());
    await t.type(tid(page, "amount"), "1000000");
    await page.getByText("You get back").first().waitFor();
    await t.zoom(page.getByText("You get back").first().locator("xpath=../.."), { hold: 2600 });
    await t.step("At maturity, she wants the money paid out", { hold: 400 });
    await t.click(tid(page, "review"));
    await t.click(tid(page, "at-maturity-pay_out"));
    await t.read(1400);
    await t.click(tid(page, "confirm"));
    await tid(page, "method-card").waitFor();
    await t.step("Card and bank transfer are there too", { hold: 500 });
    await t.click(tid(page, "method-card"));
    await t.read(2400);
    await t.hover(tid(page, "pay-by-bank"));
    await t.read(800);
    await t.step("Mobile money prompts her own number", { hold: 400 });
    await t.click(tid(page, "method-mobile-money"));
    await t.click(tid(page, "network-airtel"));
    await t.read(900);
    await t.click(tid(page, "pay-submit"));
    await page.getByText("Approve on your phone").waitFor();
    await t.read(2200);
    await t.step("Confirmed in seconds, with a signed receipt", { hold: 300 });
    await t.cut(() => tid(page, "receipt-done").waitFor({ timeout: 90000 }));
    await t.read(3200);
    await t.step("Her money earns interest from tonight", { hold: 300 });
    await t.click(tid(page, "receipt-done"));
    await page.getByText(/Earning UGX/).first().waitFor();
    await t.zoom(page.getByText(/Earning UGX/).first().locator("xpath=.."), { hold: 2400 });
    await t.read(800);
    await t.stop();
  },
};
