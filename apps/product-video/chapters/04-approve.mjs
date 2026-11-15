import { signInOnCamera, menu, confirmIfAsked } from "../lib/console.mjs";

export default {
  id: "04",
  title: "Behind the counter: approving Esther",
  subtitle: "Staff check every new client before money moves.",
  steps: [
    { caption: "Staff sign in with a password and a second step", does: "admin.finvestug.com: Ruth's email, masked password, authenticator code" },
    { caption: "The Overview shows client money and what needs attention", does: "read the Overview, scroll" },
    { caption: "Esther's check waits in Identity checks", does: "open Identity Checks" },
    { caption: "Automatic checks compare her ID, selfie and screening", does: "zoom on the automatic checks" },
    { caption: "Ruth gives a reason and approves", does: "type the reason, Approve, Yes, approve" },
    { caption: "Approval opens the account and issues her number", does: "read the account number" },
    { caption: "Compliance keeps screening and monitoring running", does: "open Compliance, scroll the rules" },
    { caption: "Every client's record is one search away", does: "Clients, find Nabukenya, open the record" },
  ],
  takes: [{ name: "console", who: "ruth", layout: "wide" }],
  async console(t, page) {
    await page.goto("https://admin.finvestug.com/");
    await page.getByText("Staff sign-in").waitFor();
    await t.start();
    await t.step("Staff sign in with a password and a second step", { hold: 1000 });
    await signInOnCamera(t, page, "ruth");
    await t.step("The Overview shows client money and what needs attention", { hold: 1500 });
    await t.read(1500);
    await t.scroll(500, { ms: 1200 });
    await t.read(1500);
    await t.scroll(-500, { ms: 800 });
    await t.step("Esther's check waits in Identity checks", { hold: 500 });
    await t.click(menu(page, "Identity Checks").or(page.getByRole("link", { name: /Identity Checks/ })).first());
    await page.getByText("READ FROM THE ID").waitFor();
    await t.read(2200);
    await t.step("Automatic checks compare her ID, selfie and screening", { hold: 600 });
    await t.zoom(page.getByText("AUTOMATIC CHECKS").locator("xpath=.."), { hold: 2600 });
    await t.step("Ruth gives a reason and approves", { hold: 500 });
    await t.type(page.getByLabel("Reason for your decision"), "ID photos and details match. Automatic checks passed.", { delay: 25 });
    await t.click(page.getByRole("button", { name: "Approve" }));
    await t.read(1500);
    await t.click(page.getByRole("button", { name: "Yes, approve" }));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText(/Account number .* was issued/).waitFor();
    await t.step("Approval opens the account and issues her number", { hold: 400 });
    await t.zoom(page.getByText(/Account number .* was issued/), { hold: 2400 });
    await t.step("Compliance keeps screening and monitoring running", { hold: 400 });
    await t.click(menu(page, "Compliance"));
    await page.getByText("Rules that are on").waitFor();
    await t.read(1800);
    await t.scroll(650, { ms: 1400 });
    await t.read(2000);
    await t.step("Every client's record is one search away", { hold: 400 });
    await t.click(menu(page, "Clients"));
    await t.type(page.getByLabel(/Find a client/), "Nabukenya");
    await t.click(page.getByRole("button", { name: "Find", exact: true }));
    await t.wait(1500);
    const row = page.getByText("Esther Nabukenya").first();
    await t.click(row);
    await t.wait(1500);
    await t.read(2500);
    await t.scroll(500, { ms: 1200 });
    await t.read(2000);
    await t.stop();
  },
};
