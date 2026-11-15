import { ensureClientSignedIn } from "../lib/client.mjs";
import { ensureSignedIn, confirmIfAsked } from "../lib/console.mjs";

const tid = (page, id) => page.getByTestId(id).locator("visible=true");
const button = (page, text) => page.locator("button", { hasText: text }).locator("visible=true").first();
const COMPLAINT = /My bank transfer took a day/;

export default {
  id: "15",
  title: "Help when she needs it",
  subtitle: "Quick answers, a chat with the team, and complaints handled properly.",
  steps: [
    { caption: "Help starts with short answers to common questions", does: "Profile, Help, How interest works" },
    { caption: "Still unsure, Esther writes to the team", does: "Write to us, type the question, Send" },
    { caption: "Grace answers with Esther's account beside her", does: "console: Support and Messages, read the chat and the client panel" },
    { caption: "A saved reply keeps answers the same for everyone", does: "Saved replies, save one, use it, add her name, Send, Mark as solved" },
    { caption: "The answer arrives in the app", does: "Esther: Help, Grace's reply" },
    { caption: "Moses makes a formal complaint and gets a reference", does: "Moses: Complaints, Make a complaint, A payment, details, Send" },
    { caption: "Grace proposes the outcome, and Ruth confirms it", does: "Complaints: owner, Not upheld, what was done, Send for confirmation; Ruth: Confirm and close" },
    { caption: "Moses reads the answer, with every step recorded", does: "Moses: the complaint, closed with the outcome" },
  ],
  takes: [
    { name: "ask", who: "esther-phone", layout: "phone" },
    { name: "answer", who: "grace", layout: "wide" },
    { name: "reply", who: "esther-phone", layout: "phone" },
    { name: "complain", who: "moses-phone", layout: "phone" },
    { name: "propose", who: "grace", layout: "wide" },
    { name: "close", who: "ruth", layout: "wide" },
    { name: "outcome", who: "moses-phone", layout: "phone" },
  ],
  async ask(t, page) {
    await ensureClientSignedIn(page, "esther", "/");
    await t.start();
    await t.step("Help starts with short answers to common questions", { hold: 800 });
    await t.click(page.locator("[aria-label=Profile]").last());
    await t.click(tid(page, "profile-help"));
    await tid(page, "chat-input").waitFor();
    await t.read(1600);
    await t.click(page.getByText("How interest works").locator("visible=true").first());
    await tid(page, "answer-to-chat").waitFor();
    await t.read(3600);
    await t.step("Still unsure, Esther writes to the team", { hold: 300 });
    await t.click(tid(page, "answer-to-chat"));
    await t.type(tid(page, "chat-input"), "Hello. When my school fees bill matures, how soon does the money reach my Airtel number?", { delay: 30 });
    await t.click(tid(page, "chat-send"));
    await page.getByText("how soon does the money reach").last().waitFor();
    await t.read(1800);
    await t.stop();
  },
  async answer(t, page) {
    await ensureSignedIn(page, "grace", "/overview");
    await t.start();
    await t.step("Grace answers with Esther's account beside her", { hold: 800 });
    await t.click(page.getByRole("link", { name: /Support and Messages/ }));
    await page.getByText("About this client").waitFor();
    await t.read(2400);
    await t.zoom(page.getByText("About this client").locator("xpath=.."), { hold: 2200 });
    await t.step("A saved reply keeps answers the same for everyone", { hold: 300 });
    await t.click(button(page, "Saved replies"));
    await t.type(page.getByLabel("Name on the button"), "Maturity payouts");
    await t.type(
      page.getByLabel("What it says"),
      "When your investment matures, we send the money to your approved payout account and tell you here when it has gone.",
      { delay: 18 },
    );
    await t.click(button(page, "Save reply"));
    await t.wait(800);
    await t.click(button(page, "Inbox"));
    await t.click(button(page, "Maturity payouts"));
    await t.read(900);
    const box = page.getByPlaceholder("Write a reply");
    // The saved reply is written for anyone: add her name before it and a line after it.
    await t.click(box, { after: 200 });
    await page.keyboard.press("Control+Home");
    await page.keyboard.type("Hello Esther. ", { delay: 45 });
    await page.keyboard.press("Control+End");
    await page.keyboard.type(" Your Airtel number is approved, so there is nothing more to do.", { delay: 25 });
    if (!(await box.inputValue()).startsWith("Hello Esther. When your investment matures")) throw new Error("The reply was not put together");
    await t.read(1200);
    await t.click(page.locator("button", { hasText: /^Send$/ }).locator("visible=true"));
    await page.getByText("Your Airtel number is approved").last().waitFor();
    await t.read(1600);
    await t.click(button(page, "Mark as solved"));
    await page.getByText("No one is waiting").waitFor();
    await t.read(1400);
    await t.stop();
  },
  async reply(t, page) {
    await ensureClientSignedIn(page, "esther", "/help");
    await page.getByText("Grace from Finvest").first().waitFor();
    await t.start();
    await t.step("The answer arrives in the app", { hold: 800 });
    await t.read(3000);
    await t.stop();
  },
  async complain(t, page) {
    await ensureClientSignedIn(page, "moses", "/help");
    await tid(page, "open-complaints").waitFor();
    await t.start();
    await t.step("Moses makes a formal complaint and gets a reference", { hold: 800 });
    await t.click(tid(page, "open-complaints"));
    await t.read(1800);
    await t.click(tid(page, "new-complaint"));
    await t.click(tid(page, "category-payments"));
    await t.type(tid(page, "complaint-summary"), "My bank transfer took a day to show in the app", { delay: 30 });
    await t.type(
      tid(page, "complaint-details"),
      "I paid UGX 2,000,000 by bank transfer on 15 November. The app said it was being checked until the next day. Please tell me why it took so long.",
      { delay: 15 },
    );
    await t.click(tid(page, "send-complaint"));
    await page.getByText("We have your complaint").waitFor();
    await t.zoom(page.getByText(/^reference$/i).locator("visible=true").locator("xpath=../.."), { hold: 2200 });
    await t.read(1600);
    await t.stop();
  },
  async propose(t, page) {
    await ensureSignedIn(page, "grace", "/complaints");
    await page.getByRole("button", { name: COMPLAINT }).waitFor();
    await t.start();
    await t.step("Grace proposes the outcome, and Ruth confirms it", { hold: 800 });
    await t.read(1800);
    await t.click(page.getByRole("button", { name: COMPLAINT }));
    await t.read(1200);
    await page.locator("select").locator("visible=true").first().selectOption({ label: "Grace Atim" });
    await page.getByText(/Assigned, Grace Atim/).waitFor();
    await t.read(900);
    await t.click(button(page, "Propose the outcome"));
    await t.click(page.getByLabel("Not upheld. Finvest did nothing wrong"));
    await t.type(
      page.locator("textarea").locator("visible=true").first(),
      "Bank transfers are matched to our bank statement, which comes the next working day. Your payment was confirmed on 16 November and bought your bill at the next auction. Nothing was lost.",
      { delay: 12 },
    );
    await t.click(page.getByRole("radio", { name: "Yes" }));
    await t.click(button(page, "Send for confirmation"));
    await confirmIfAsked(t, page, "grace");
    await page.getByText("Outcome waiting for a second person").waitFor();
    await t.read(2000);
    await t.stop();
  },
  async close(t, page) {
    await ensureSignedIn(page, "ruth", "/complaints");
    await page.getByRole("button", { name: COMPLAINT }).waitFor();
    await t.start();
    await t.click(page.getByRole("button", { name: COMPLAINT }));
    await button(page, "Confirm and close").waitFor();
    await t.read(2200);
    await t.click(button(page, "Confirm and close"));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText(/Closed, Ruth Achieng/).waitFor();
    await t.read(1200);
    await t.zoom(page.getByText("History").locator("xpath=.."), { hold: 2200 });
    await t.stop();
  },
  async outcome(t, page) {
    await ensureClientSignedIn(page, "moses", "/support/complaints");
    await page.getByText(/C-\d+/).first().waitFor();
    await t.start();
    await t.step("Moses reads the answer, with every step recorded", { hold: 800 });
    await t.click(page.getByText(/C-\d+/).first());
    await page.getByText("Nothing was lost").first().waitFor();
    await t.read(3400);
    await t.stop();
  },
};
