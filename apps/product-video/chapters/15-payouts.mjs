import { CLIENT } from "../lib/people.mjs";
import { lastSms, nextSms } from "../lib/session.mjs";
import { ensureClientSignedIn } from "../lib/client.mjs";
import { ensureSignedIn, confirmIfAsked } from "../lib/console.mjs";

const tid = (page, id) => page.getByTestId(id).locator("visible=true");
const ACCOUNT = /Esther Nabukenya Airtel/;

async function openAccount(page, who) {
  await ensureSignedIn(page, who, "/review-queue?tab=accounts");
  await page.getByRole("button", { name: ACCOUNT }).waitFor();
}

export default {
  id: "15",
  title: "Getting paid",
  subtitle: "Money only goes to an account in her own name.",
  steps: [
    { caption: "Each investment shows its rate, its term and its return", does: "Portfolio, the UGX 1,000,000 bill" },
    { caption: "She can change what happens at maturity until the day before", does: "Change what happens at the end, read the four choices" },
    { caption: "To be paid, she adds her own Airtel number", does: "Add a payout account, Airtel number, her name, Save and text me a code" },
    { caption: "A texted code proves the number is hers", does: "type the code from the SMS, Confirm" },
    { caption: "The network's name for the number is checked against her ID", does: "Grace: Review Queue, Payout accounts, read the names" },
    { caption: "Grace records the check, and Ruth approves it", does: "Grace: reason, Record the check. Ruth: reason, Approve the check, Yes, approve" },
    { caption: "Withdrawing early shows exactly what she would get", does: "Esther: Withdraw early, read it, go back without confirming" },
  ],
  takes: [
    { name: "phone", who: "esther-phone", layout: "phone" },
    { name: "grace", who: "grace", layout: "wide" },
    { name: "ruth", who: "ruth", layout: "wide" },
    { name: "early", who: "esther-phone", layout: "phone" },
  ],
  async phone(t, page) {
    await ensureClientSignedIn(page, "esther", "/portfolio");
    await page.getByRole("button", { name: /worth UGX 1,000,/ }).waitFor();
    await t.start();
    await t.step("Each investment shows its rate, its term and its return", { hold: 900 });
    await t.click(page.getByRole("button", { name: /worth UGX 1,000,/ }));
    await tid(page, "term-progress").waitFor();
    await t.read(2400);
    await t.scroll(300, { ms: 900 });
    await t.read(1200);
    await t.step("She can change what happens at maturity until the day before", { hold: 300 });
    await t.click(tid(page, "change-maturity"));
    await tid(page, "maturity-pay_out").waitFor();
    await t.read(2400);
    await t.scroll(350, { ms: 1000 });
    await t.read(1800);
    await t.step("To be paid, she adds her own Airtel number", { hold: 400 });
    // Pay out asks for a payout account first.
    await t.click(tid(page, "maturity-pay_out"));
    await t.read(1000);
    await t.click(tid(page, "add-payout-account"));
    await tid(page, "payout-account-save").waitFor();
    await t.read(1800);
    const before = lastSms(CLIENT.phone);
    await t.click(tid(page, "payout-account-save"));
    const sms = await t.cut(() => nextSms(CLIENT.phone, before));
    await tid(page, "code-input").waitFor();
    await t.step("A texted code proves the number is hers", { hold: 300 });
    await t.sms(sms.text);
    await t.wait(1000);
    await t.type(tid(page, "code-input"), sms.code, { delay: 110 });
    await t.click(tid(page, "payout-code-submit"));
    await t.cut(async () => {
      for (let i = 0; i < 20 && !(await page.getByText("Our team is checking your proof").isVisible()); i++) {
        await page.waitForTimeout(1500);
        await page.reload();
      }
    });
    await page.getByText("Our team is checking your proof").waitFor();
    await t.read(2600);
    await t.stop();
  },
  async grace(t, page) {
    await openAccount(page, "grace");
    await t.start();
    await t.step("The network's name for the number is checked against her ID", { hold: 600 });
    await t.click(page.getByRole("button", { name: ACCOUNT }));
    await page.getByRole("heading", { name: "The proof" }).waitFor();
    await t.zoom(page.getByText("Registered name on the number").locator("xpath=../.."), { hold: 2400 });
    await t.step("Grace records the check, and Ruth approves it", { hold: 300 });
    await t.type(page.getByLabel("Reason for your decision"), "Network name matches the name on her ID.", { delay: 25 });
    await t.click(page.getByRole("button", { name: "Record the check" }).last());
    await confirmIfAsked(t, page, "grace");
    await page.getByText("You made this check, so a different person must approve it.").waitFor();
    await t.read(1800);
    await t.stop();
  },
  async ruth(t, page) {
    await openAccount(page, "ruth");
    await t.start();
    await t.click(page.getByRole("button", { name: ACCOUNT }));
    await page.getByRole("button", { name: "Approve the check" }).first().waitFor();
    await t.read(1500);
    await t.click(page.getByRole("button", { name: "Approve the check" }).first());
    await t.type(page.getByLabel("Reason for your decision"), "Checked the network name against her ID.", { delay: 25 });
    await t.click(page.getByRole("button", { name: "Approve the check" }).last());
    await t.click(page.getByRole("button", { name: "Yes, approve" }));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText(/Payout accounts \(0\)|Nothing is waiting|approved/i).first().waitFor();
    await t.read(2000);
    await t.stop();
  },
  async early(t, page) {
    await ensureClientSignedIn(page, "esther", "/payouts");
    await page.getByText("Airtel Mobile Money").first().waitFor();
    await t.start();
    await t.read(1800);
    await t.step("Withdrawing early shows exactly what she would get", { hold: 300 });
    await t.cut(() => page.goto("https://app.finvestug.com/portfolio"));
    await t.click(page.getByRole("button", { name: /worth UGX 1,000,/ }));
    await t.click(tid(page, "withdraw-early"));
    await tid(page, "withdraw-amount").waitFor();
    await t.read(3000);
    await t.scroll(300, { ms: 900 });
    await t.read(2200);
    await t.click(page.getByRole("button", { name: "Back" }).locator("visible=true"));
    await t.read(1200);
    await t.stop();
  },
};
