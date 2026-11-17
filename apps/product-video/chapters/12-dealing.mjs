import { ensureSignedIn, menu, confirmIfAsked } from "../lib/console.mjs";

// The Bank of Uganda's next 91 day auction, and what it would sell at the 21 Oct 2026 yield of
// 11.249%: UGX 1,300,000 of face value for 1,300,000 / (1 + 0.11249 x 91 / 364) = 1,264,441.
const AUCTION = { day: "2026-11-18", settled: "2026-11-19", matures: "2027-02-18", face: "1300000", cash: "1264441", reference: "BOU-CSD-1237-0091-0042" };

async function desk(page, who) {
  await ensureSignedIn(page, who, "/dealing-desk");
  await page.getByText("Auction dates").first().waitFor();
}

export default {
  id: "12",
  title: "Buying the bills",
  subtitle: "Client money becomes real Treasury bills, checked by two people.",
  steps: [
    { caption: "The dealing desk shows client money still to place", does: "Joel: Dealing desk, read the position by product" },
    { caption: "Joel adds the Bank of Uganda's next auction date", does: "Propose an auction date: 91 Day Treasury Bill, Wed 18 Nov 2026" },
    { caption: "He prepares a bid for the 91 day bill", does: "Prepare an order: Auction bid, 91 Day Treasury Bill, UGX 1,300,000" },
    { caption: "Ruth approves the date and the bid", does: "Ruth: Approve the auction date, Approve as second person" },
    { caption: "After the auction, Joel records what was bought", does: "Thursday: Record the result, face, cash paid, settled, matures, custodian reference" },
    { caption: "Ruth checks the custodian's note and posts it", does: "Ruth: Confirm against the custodian's note, Yes, confirm and post" },
    { caption: "Client money now sits in real bills", does: "read Bills held for clients" },
    { caption: "Staff see every investment, goal and plan", does: "Investments, Goals, Automatic Investing, Products" },
  ],
  takes: [
    { name: "order", who: "joel", layout: "wide" },
    { name: "approve", who: "ruth", layout: "wide" },
    { name: "result", who: "joel", layout: "wide", clock: "+3" },
    { name: "confirm", who: "ruth", layout: "wide" },
  ],
  async order(t, page) {
    await desk(page, "joel");
    await t.start();
    await t.step("The dealing desk shows client money still to place", { hold: 1200 });
    await t.zoom(page.getByText("Position by product").locator("xpath=.."), { hold: 2600, scale: 1.2 });
    await t.step("Joel adds the Bank of Uganda's next auction date", { hold: 400 });
    const dateForm = page.getByText("Propose an auction date").locator("xpath=ancestor::form[1]");
    await dateForm.scrollIntoViewIfNeeded();
    await t.read(900);
    await dateForm.locator("select").selectOption({ label: "91 Day Treasury Bill" });
    await t.click(dateForm.locator('input[placeholder="dd/mm/yyyy"]'));
    await dateForm.locator('input[placeholder="dd/mm/yyyy"]').fill(AUCTION.day);
    await t.read(600);
    await t.click(page.getByRole("button", { name: "Propose the date" }));
    await confirmIfAsked(t, page, "joel");
    await page.getByText("Waiting for a second person").first().waitFor();
    await t.read(1400);
    await t.step("He prepares a bid for the 91 day bill", { hold: 400 });
    await page.getByRole("button", { name: "Prepare an order" }).scrollIntoViewIfNeeded();
    await t.click(page.getByRole("button", { name: "Prepare an order" }));
    const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Send for approval" }) }).first();
    await form.getByLabel("Product").selectOption({ label: "91 Day Treasury Bill" });
    await t.read(800);
    await t.type(form.getByLabel("Face value (UGX)"), AUCTION.face);
    await t.read(1200);
    await t.click(form.getByRole("button", { name: "Send for approval" }));
    await confirmIfAsked(t, page, "joel");
    await page.getByText("You prepared this order, so someone else has to approve it.").waitFor();
    await t.zoom(page.getByText("Auction bid, 91 Day Treasury Bill").locator("xpath=.."), { hold: 2000 });
    await t.stop();
  },
  async approve(t, page) {
    await desk(page, "ruth");
    await page.getByRole("button", { name: /Approve the 91 Day Treasury Bill auction/ }).scrollIntoViewIfNeeded();
    await t.start();
    await t.step("Ruth approves the date and the bid", { hold: 1400 });
    await t.click(page.getByRole("button", { name: /Approve the 91 Day Treasury Bill auction/ }));
    await confirmIfAsked(t, page, "ruth");
    await page.getByRole("button", { name: /Cancel date for the 91 Day/ }).waitFor();
    await t.read(1000);
    await t.click(page.getByRole("button", { name: "Approve as second person" }));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText("Approved, waiting for the result").waitFor();
    await t.read(2200);
    await t.stop();
  },
  async result(t, page) {
    await desk(page, "joel");
    await page.getByRole("button", { name: "Record the result" }).scrollIntoViewIfNeeded();
    await t.start();
    await t.step("After the auction, Joel records what was bought", { hold: 1200 });
    await t.click(page.getByRole("button", { name: "Record the result" }));
    const f = page.getByRole("form", { name: "Record the result" });
    await t.type(f.getByLabel("Face value bought (UGX)"), AUCTION.face);
    await t.type(f.getByLabel("Cash paid (UGX)"), AUCTION.cash);
    await t.click(f.getByLabel("Settled on"));
    await f.getByLabel("Settled on").fill(AUCTION.settled);
    await t.click(f.getByLabel("Matures on"));
    await f.getByLabel("Matures on").fill(AUCTION.matures);
    await t.type(f.getByLabel("Custodian reference"), AUCTION.reference, { delay: 30 });
    await t.read(1200);
    await t.click(f.getByRole("button", { name: "Record the result" }));
    await confirmIfAsked(t, page, "joel");
    await page.getByText("You recorded this result, so someone else has to confirm it.").waitFor();
    await t.read(2400);
    await t.stop();
  },
  async confirm(t, page) {
    await desk(page, "ruth");
    await page.getByRole("button", { name: "Confirm against the custodian's note" }).scrollIntoViewIfNeeded();
    await t.start();
    await t.step("Ruth checks the custodian's note and posts it", { hold: 1400 });
    await t.click(page.getByRole("button", { name: "Confirm against the custodian's note" }));
    await t.zoom(page.getByRole("group", { name: "Confirm the result" }), { hold: 2000 });
    await t.click(page.getByRole("button", { name: "Yes, confirm and post" }));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText("No open orders").waitFor();
    await t.step("Client money now sits in real bills", { hold: 300 });
    await page.evaluate(() => window.scrollTo({ top: 0 }));
    await t.zoom(page.getByText("Bills held for clients").locator("xpath=.."), { hold: 2400 });
    await t.step("Staff see every investment, goal and plan", { hold: 400 });
    for (const name of ["Investments", "Goals", "Automatic Investing", "Products"]) {
      await t.click(menu(page, name));
      await page.waitForLoadState("networkidle");
      await t.read(2200);
      if (name === "Investments") {
        await t.scroll(400, { ms: 1000 });
        await t.read(1500);
      }
    }
    await t.stop();
  },
};
