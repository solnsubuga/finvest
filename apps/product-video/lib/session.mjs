// Browser contexts for the story: the client's phone, the client's laptop, the staff console
// and the test inbox. Sign-ins, passkeys and the story clock carry from one chapter to the
// next through files in $WORK, so any chapter can be re-recorded on its own after a restore.
import fs from "node:fs";
import path from "node:path";
import { launch, WORK } from "./browser.mjs";
import { Take } from "./take.mjs";

export const LAYOUTS = {
  wide: { viewport: { width: 1440, height: 810 }, deviceScaleFactor: 1 },
  phone: { viewport: { width: 390, height: 760 }, deviceScaleFactor: 2, isMobile: true, hasTouch: false },
  mail: { viewport: { width: 1440, height: 810 }, deviceScaleFactor: 1 },
};

export function offsetDays() {
  try {
    return Number(fs.readFileSync(path.join(WORK, "offset"), "utf8").trim()) || 0;
  } catch {
    return 0;
  }
}

/** Whole days from the real date (UTC) to day 0 of the story, as stack/env.sh counts them. */
function realShiftDays() {
  const day0 = Date.parse(`${process.env.PV_DAY0 ?? "2026-11-15"}T00:00:00Z`);
  const today = Date.parse(`${new Date().toISOString().slice(0, 10)}T00:00:00Z`);
  return Math.round((day0 - today) / 86_400_000);
}

/** The story's "now" in milliseconds: the real time moved to the story day. */
export function storyNow() {
  return Date.now() + (offsetDays() + realShiftDays()) * 86_400_000;
}

let browser;
export async function browserOnce() {
  browser ??= await launch();
  return browser;
}

export async function closeBrowser() {
  await browser?.close();
  browser = undefined;
}

/**
 * @param {string} who   a name for the saved state, e.g. "esther-phone" or "ruth"
 * @param {"wide"|"phone"|"mail"} layout
 */
export async function openContext(who, layout) {
  const b = await browserOnce();
  const stateFile = path.join(WORK, `state-${who}.json`);
  const context = await b.newContext({
    ...LAYOUTS[layout],
    locale: "en-GB",
    timezoneId: "Africa/Kampala",
    colorScheme: "light",
    storageState: fs.existsSync(stateFile) ? stateFile : undefined,
  });
  await Take.prepare(context);
  if (offsetDays() + realShiftDays() !== 0) await context.clock.install({ time: storyNow() });
  const page = await context.newPage();
  const passkeys = await virtualAuthenticator(page, who);
  return {
    context,
    page,
    async save() {
      // Each kept on its own, so one failing never loses the other.
      await context.storageState({ path: stateFile }).catch((e) => console.error(`Could not save ${who}'s sign-in: ${e.message}`));
      await passkeys.save().catch((e) => console.error(`Could not save ${who}'s passkeys: ${e.message}`));
    },
    async close() {
      await this.save();
      await context.close();
    },
  };
}

// A platform authenticator (Touch ID, a phone's screen lock) that says yes at once. Its keys are
// kept per device, so a passkey made on the phone in one chapter still works on the phone in a
// later one, and the laptop is a device of its own that needs its own passkey.
async function virtualAuthenticator(page, who) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable", { enableUI: false });
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  const file = path.join(WORK, `passkeys-${who}.json`);
  // Backups from before keys were kept per device name the phone's keys after the person.
  const older = path.join(WORK, `passkeys-${who.replace(/-phone$/, "")}.json`);
  const source = fs.existsSync(file) ? file : who.endsWith("-phone") && fs.existsSync(older) ? older : null;
  if (source) {
    for (const credential of JSON.parse(fs.readFileSync(source, "utf8"))) {
      await cdp.send("WebAuthn.addCredential", { authenticatorId, credential }).catch((e) => console.error(`A saved passkey could not be loaded: ${e.message}`));
    }
  }
  return {
    async save() {
      const { credentials } = await cdp.send("WebAuthn.getCredentials", { authenticatorId });
      if (credentials.length) fs.writeFileSync(file, JSON.stringify(credentials));
    },
  };
}

/** The newest text message the local SMS backend printed for a number, and its code. */
export function lastSms(phone) {
  const log = fs.readFileSync(path.join(WORK, "logs/worker.log"), "utf8");
  const wanted = phone.replace(/^0/, "+256");
  const all = [...log.matchAll(/\[local SMS to (\+\d+)\] (.*)/g)].filter((m) => m[1] === wanted);
  const text = all.at(-1)?.[2];
  return text ? { text, code: text.match(/\b(\d{6})\b/)?.[1] } : undefined;
}

/** Wait for a new text message to reach a number. */
export async function nextSms(phone, before, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const sms = lastSms(phone);
    if (sms && sms.text !== before?.text) return sms;
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`No text message reached ${phone}`);
}
