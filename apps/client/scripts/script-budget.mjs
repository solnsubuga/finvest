// Fails the web build when the script a first-time visitor downloads before the first screen
// grows past its budget (QA F-011, 15 November 2026). Run after `expo export --platform web`.
//
// That script is every file dist/index.html loads with <script src>: Expo's entry (React, React
// Native Web, Expo Router) and the common chunk the root layout needs (the providers, the API
// client and Zod). Screens load on their own when opened. The budget sits a little above
// today's size, so a change that adds to the first download has to raise it on purpose, in
// review, with the reason in the commit.
import { readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const BUDGET_BYTES = 1_950_000;

const dist = join(dirname(fileURLToPath(import.meta.url)), "..", "dist");
const html = readFileSync(join(dist, "index.html"), "utf8");
const scripts = [...html.matchAll(/<script[^>]*\ssrc="(\/_expo\/static\/js\/web\/[^"]+\.js)"/g)].map((match) => match[1]);
if (scripts.length === 0) {
  console.error("dist/index.html loads no scripts from /_expo/static/js/web/: has Expo's output changed?");
  process.exit(1);
}
let total = 0;
for (const path of new Set(scripts)) {
  const bytes = statSync(join(dist, path)).size;
  total += bytes;
  console.log(`${String(Math.round(bytes / 1024)).padStart(6)} KB  ${path}`);
}
const line = `First load script: ${Math.round(total / 1024)} KB of a ${Math.round(BUDGET_BYTES / 1024)} KB budget`;
if (total > BUDGET_BYTES) {
  console.error(`${line}. Load the new code on demand, or raise BUDGET_BYTES in scripts/script-budget.mjs and say why.`);
  process.exit(1);
}
console.log(line);
