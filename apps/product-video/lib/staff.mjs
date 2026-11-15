// Staff sign-in on camera: email, the masked password, then the authenticator code at the
// story clock. A code works once, so the windows used per person are remembered and never
// used again. The story clock can go back (a restore), so only the exact windows are avoided.
import fs from "node:fs";
import path from "node:path";
import { WORK } from "./browser.mjs";
import { STAFF, STAFF_PASSWORD } from "./people.mjs";
import { totp } from "./totp.mjs";
import { storyNow } from "./session.mjs";

const usedFile = () => path.join(WORK, "totp-used.json");

export function staffSecret(who) {
  return JSON.parse(fs.readFileSync(path.join(WORK, "staff.json"), "utf8"))[STAFF[who].email].secret;
}

function readUsed() {
  const used = fs.existsSync(usedFile()) ? JSON.parse(fs.readFileSync(usedFile(), "utf8")) : {};
  for (const k of Object.keys(used)) if (!Array.isArray(used[k])) used[k] = [used[k]];
  return used;
}

export function markUsed(email, window) {
  const used = readUsed();
  used[email] = [...(used[email] ?? []), window].slice(-500);
  fs.writeFileSync(usedFile(), JSON.stringify(used));
}

/** A code from a window not used before, with at least 8 seconds of life left. */
export async function codeFor(email, secret) {
  const used = readUsed();
  for (;;) {
    const now = storyNow();
    const window = Math.floor(now / 30000);
    const left = 30 - (Math.floor(now / 1000) % 30);
    if (!(used[email] ?? []).includes(window) && left >= 8) {
      markUsed(email, window);
      return totp(secret, now);
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

export async function staffCode(who) {
  return codeFor(STAFF[who].email, staffSecret(who));
}
export { STAFF_PASSWORD };
