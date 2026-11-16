import { ensureClientSignedIn } from "../lib/client.mjs";

const tid = (page, id) => page.getByTestId(id).locator("visible=true");
const INVESTMENT = /worth UGX 1,00/;
// The join link carries a one-time token, so it is made and opened off camera and only passed
// from one take to the next here.
let joinLink = null;

export default {
  id: "16",
  title: "Sending and saving together",
  subtitle: "Part of an investment can go to family, and a goal can be shared.",
  steps: [
    { caption: "Esther sends part of an investment to her brother", does: "Portfolio, the bill, Send to someone, Moses's number, Find" },
    { caption: "She picks an amount, adds a note and a reason", does: "type UGX 100,000, the message, Family support" },
    { caption: "The money stays invested on its way", does: "Check and send, Send to Moses, the transfer status" },
    { caption: "A group goal saves for something together", does: "Goals, Save together, name, target, twelve months, her part" },
    { caption: "Moses sees the investment he was sent", does: "Moses: Portfolio, the received 91 day bill" },
    { caption: "He joins the group with his own part", does: "Moses opens the invite, types his part, Join the group" },
    { caption: "Moses helps with Daniel's fees from his own phone", does: "Esther: Invest, 91 day bill, UGX 200,000, Confirm and pay, Pay from another number: Moses's" },
    { caption: "He pays on Pesapal's page, and it counts as hers", does: "Pay, Open the secure page; the sandbox completes; the receipt names his number, masked (zoom)" },
  ],
  takes: [
    { name: "send", who: "esther-phone", layout: "phone", clock: "+1" },
    { name: "group", who: "esther-phone", layout: "phone" },
    { name: "moses", who: "moses-phone", layout: "phone" },
    { name: "join", who: "moses-phone", layout: "phone" },
    { name: "uncle", who: "esther-phone", layout: "phone" },
  ],
  async send(t, page) {
    await ensureClientSignedIn(page, "esther", "/portfolio");
    await page.getByRole("button", { name: INVESTMENT }).first().waitFor();
    await t.start();
    await t.step("Esther sends part of an investment to her brother", { hold: 800 });
    await t.click(page.getByRole("button", { name: INVESTMENT }).first());
    await t.click(tid(page, "send-investment"));
    await t.type(tid(page, "send-to"), "0700000736");
    await t.click(tid(page, "find-recipient"));
    await tid(page, "send-amount").waitFor();
    await t.read(1600);
    await t.step("She picks an amount, adds a note and a reason", { hold: 300 });
    await t.type(tid(page, "send-amount"), "100000");
    await t.type(tid(page, "send-message"), "For the twins' school uniforms", { delay: 35 });
    await t.click(tid(page, "send-purpose-family_support"));
    await t.read(1200);
    await t.step("The money stays invested on its way", { hold: 300 });
    await t.click(tid(page, "review-send"));
    await tid(page, "confirm-send").waitFor();
    await t.read(2600);
    await t.click(tid(page, "confirm-send"));
    await t.cut(() => page.getByText(/On its way to Moses|Sent to Moses|To Moses O\./).first().waitFor({ timeout: 60000 }));
    await t.read(2800);
    await t.stop();
  },
  async group(t, page) {
    await ensureClientSignedIn(page, "esther", "/goals");
    await t.start();
    await t.step("A group goal saves for something together", { hold: 800 });
    await t.cut(() => page.goto("https://app.finvestug.com/goals/groups/new"));
    await tid(page, "group-name").waitFor();
    await t.click(tid(page, "group-category-other"));
    await t.type(tid(page, "group-name"), "A new roof for Grandma's house", { delay: 35 });
    await t.type(tid(page, "group-target"), "6000000");
    await t.click(tid(page, "group-horizon-12"));
    await t.type(tid(page, "group-part"), "2000000");
    await t.type(tid(page, "group-you"), "Esther");
    await t.read(1600);
    await t.click(tid(page, "group-create"));
    await tid(page, "group-make-link").waitFor({ timeout: 30000 });
    await t.read(2600);
    await t.stop();
    // Off camera: make the invite link and keep it for Moses's take.
    await tid(page, "group-make-link").click();
    await tid(page, "group-link").waitFor();
    // The code is hidden until asked for.
    await tid(page, "group-link-show").click();
    await page.waitForTimeout(500);
    joinLink = (await tid(page, "group-link").innerText()).trim().match(/https:\/\/\S+/)?.[0];
    if (!joinLink) throw new Error("No invite link on the group goal");
  },
  async moses(t, page) {
    await ensureClientSignedIn(page, "moses", "/portfolio");
    await page.getByRole("button", { name: /UGX 100,/ }).first().waitFor();
    await t.start();
    await t.step("Moses sees the investment he was sent", { hold: 800 });
    await t.click(page.getByRole("button", { name: /UGX 100,/ }).first());
    await t.read(2600);
    await t.stop();
  },
  async join(t, page) {
    await ensureClientSignedIn(page, "moses", "/");
    await page.goto(joinLink);
    await tid(page, "join-part").waitFor();
    await t.start();
    await t.step("He joins the group with his own part", { hold: 800 });
    await t.read(1800);
    await t.type(tid(page, "join-part"), "2000000");
    await t.type(tid(page, "join-you"), "Moses");
    await t.click(tid(page, "group-join"));
    await t.cut(() => page.getByText("A new roof for Grandma's house").first().waitFor({ timeout: 30000 }));
    await t.read(2800);
    await t.stop();
  },
  async uncle(t, page) {
    await ensureClientSignedIn(page, "esther", "/invest");
    await page.getByText("91 Day Treasury Bill").first().waitFor();
    await t.start();
    await t.step("Moses helps with Daniel's fees from his own phone", { hold: 800 });
    await t.click(page.getByText("91 Day Treasury Bill").first());
    await t.type(tid(page, "amount"), "200000");
    await t.click(tid(page, "review"));
    await tid(page, "confirm").waitFor();
    await t.read(1000);
    await t.click(tid(page, "confirm"));
    await tid(page, "method-mobile-money").waitFor();
    await t.click(tid(page, "network-airtel"));
    await t.click(tid(page, "pay-number-toggle"));
    await t.type(tid(page, "pay-other-number"), "700000736", { delay: 90 });
    await t.read(1800);
    await t.step("He pays on Pesapal's page, and it counts as hers", { hold: 300 });
    await t.click(tid(page, "pay-submit"));
    await tid(page, "deposit-open-page").waitFor({ timeout: 30000 });
    await t.read(2800);
    await t.click(tid(page, "deposit-open-page"));
    await t.cut(() => page.getByText("Completed").first().waitFor({ timeout: 90000 }));
    await t.read(1200);
    await t.zoom(page.getByText("Paid from").first().locator("xpath=../.."), { hold: 2800 });
    await t.stop();
  },
};
