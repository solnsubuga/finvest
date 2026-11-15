import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WORK } from "../lib/browser.mjs";
import { ensureSignedIn, signInOnCamera, confirmIfAsked } from "../lib/console.mjs";
import { STAFF } from "../lib/people.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const GRACE = STAFF.grace;

/** The newest invitation email for a member of staff, and the one-time link in it. */
function invitationLink(email) {
  const dir = path.join(WORK, "mail");
  for (const f of fs.readdirSync(dir).sort().reverse()) {
    const m = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
    const to = Array.isArray(m.to) ? m.to : [m.to];
    if (to.includes(email) && m.subject.startsWith("Set up your Finvest staff account")) {
      return m.html.match(/href="(https:\/\/admin\.finvestug\.com\/enrol#[^"]+)"/)[1];
    }
  }
  throw new Error(`No invitation for ${email}`);
}

export default {
  id: "12",
  title: "Growing the team",
  subtitle: "Two admins approve every change to who can do what.",
  steps: [
    { caption: "Each role opens only what that job needs", does: "Ruth: Team and permissions, scroll the roles table" },
    { caption: "Ruth invites Grace Atim to operations", does: "Invite someone: Grace Atim, Operations, a reason, Send for approval" },
    { caption: "A second admin, Sarah, approves the invitation", does: "Sarah: Approve, a reason, Approve the change" },
    { caption: "Grace gets a one-time link by email", does: "test inbox: Set up your Finvest staff account (the link printed in it is hidden)" },
    { caption: "She signs in with her password and authenticator code", does: "Grace sets up off camera (her app's key is never shown), then signs in" },
    { caption: "Her menu shows only operations work", does: "read the menu" },
  ],
  takes: [
    { name: "invite", who: "ruth", layout: "wide" },
    { name: "approve", who: "sarah", layout: "wide" },
    { name: "email", who: "inbox", layout: "mail" },
    { name: "grace", who: "grace", layout: "wide" },
  ],
  // Sarah Namatovu joins off camera, as the company's second admin.
  async before() {
    execFileSync(path.join(here, "../setup/add-staff.sh"), [STAFF.sarah.email, STAFF.sarah.name, "admin"], { stdio: "inherit" });
  },
  async invite(t, page) {
    await ensureSignedIn(page, "ruth", "/team");
    await page.getByRole("heading", { name: "What each role can do" }).waitFor();
    await t.start();
    await t.step("Each role opens only what that job needs", { hold: 1400 });
    await page.getByRole("heading", { name: "What each role can do" }).scrollIntoViewIfNeeded();
    await t.read(1500);
    await t.fast(() => t.scroll(900, { ms: 2400 }), 2);
    await t.read(1200);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
    await page.locator("main").evaluate((m) => m.scrollIntoView());
    await t.step("Ruth invites Grace Atim to operations", { hold: 400 });
    await t.click(page.getByRole("button", { name: "Invite someone" }));
    const f = page.locator("form").filter({ has: page.getByRole("button", { name: "Send for approval" }) }).last();
    await t.type(f.getByLabel("Full name"), GRACE.name);
    await t.type(f.getByLabel("Work email"), GRACE.email, { delay: 30 });
    await f.getByLabel("Role").selectOption({ label: "Operations" });
    await t.read(500);
    await t.type(f.getByLabel(/Why/), "Grace joins operations to check payout accounts and answer clients.", { delay: 25 });
    await t.click(f.getByRole("button", { name: "Send for approval" }));
    await confirmIfAsked(t, page, "ruth");
    await page.getByText("You proposed this, so another admin approves it.").waitFor();
    await t.read(2400);
    await t.stop();
  },
  async approve(t, page) {
    await ensureSignedIn(page, "sarah", "/team");
    await page.getByRole("button", { name: /^Approve/ }).first().waitFor();
    await t.start();
    await t.step("A second admin, Sarah, approves the invitation", { hold: 1600 });
    await t.click(page.getByRole("button", { name: /^Approve/ }).first());
    await t.type(page.getByLabel(/Why approve it/), "Agreed at the operations meeting.", { delay: 30 });
    await t.click(page.getByRole("button", { name: "Approve the change" }));
    await confirmIfAsked(t, page, "sarah");
    await page.getByText("Approved. The change is in effect.").waitFor();
    await t.read(1200);
    await t.zoom(page.getByText(GRACE.email).first().locator("xpath=../.."), { hold: 2000 });
    await t.stop();
  },
  async email(t, page) {
    await page.goto("https://mail.local.test/");
    await t.start();
    await t.step("Grace gets a one-time link by email", { hold: 800 });
    await t.click(page.locator("nav a", { hasText: "Set up your Finvest staff account" }).first());
    await t.read(3400);
    await t.stop();
    // Off camera: Grace opens her link, chooses a password and adds an authenticator app. The
    // app's key is a secret, so this part is not filmed.
    execFileSync("node", [path.join(here, "../setup/enrol-staff.mjs"), GRACE.email, invitationLink(GRACE.email)], { stdio: "inherit" });
  },
  async grace(t, page) {
    await page.goto("https://admin.finvestug.com/");
    await page.getByText("Staff sign-in").waitFor();
    await t.start();
    await t.step("She signs in with her password and authenticator code", { hold: 1000 });
    await signInOnCamera(t, page, "grace");
    await t.step("Her menu shows only operations work", { hold: 400 });
    await t.zoom(page.locator("nav").first(), { hold: 2600, scale: 1.2 });
    await t.read(1200);
    await t.stop();
  },
};
