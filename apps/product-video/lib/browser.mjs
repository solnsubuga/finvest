// Chromium for the recording. Every Finvest host resolves to the local front door and every
// other host fails, with no proxy, so a take can never reach production or the internet.
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

export const WORK = process.env.PV_WORK ?? "/tmp/finvest-product-video";

export async function launch() {
  const spki = fs.readFileSync(path.join(WORK, "certs/spki.txt"), "utf8").trim().split("\n").join(",");
  const env = { ...process.env };
  for (const k of Object.keys(env)) if (/_proxy$/i.test(k)) delete env[k];
  return chromium.launch({
    env,
    proxy: undefined,
    args: [
      "--no-proxy-server",
      "--host-resolver-rules=MAP finvestug.com 127.0.0.1, MAP *.finvestug.com 127.0.0.1, MAP *.local.test 127.0.0.1, MAP * ~NOTFOUND",
      `--ignore-certificate-errors-spki-list=${spki}`,
      "--font-render-hinting=none",
      "--disable-features=Translate,MediaRouter",
      "--hide-scrollbars",
    ],
  });
}
