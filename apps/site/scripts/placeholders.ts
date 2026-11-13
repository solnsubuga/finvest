/**
 * What the site still needs before launch. Run `pnpm build` first.
 *
 * 1. Every [BRACKETED] placeholder on the built pages: facts the site is waiting for, such as
 *    the licence number.
 * 2. Every claim in claims.json that nobody has signed off: statements written out in full
 *    that the owner and counsel must confirm are true. To sign one off, set its `confirmedBy`
 *    to who confirmed it and when.
 *
 * With --strict it fails if anything is left in either list. Run that before the site is
 * opened to search engines.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const dist = new URL("../dist/", import.meta.url).pathname;

function pages(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return pages(path);
    return path.endsWith(".html") ? [path] : [];
  });
}

const found = new Map<string, Set<string>>();
for (const page of pages(dist)) {
  const text = readFileSync(page, "utf-8").replace(/<script[\s\S]*?<\/script>/g, "");
  for (const match of text.matchAll(/\[[A-Z][A-Z :,'.a-z]*[A-Z.]\]/g)) {
    const where = found.get(match[0]) ?? new Set<string>();
    where.add(`/${relative(dist, page).replace(/index\.html$/, "")}`);
    found.set(match[0], where);
  }
}

for (const [placeholder, where] of [...found].sort()) {
  console.log(`${placeholder}\n    ${[...where].sort().join("  ")}`);
}
console.log(`\n${found.size} placeholder(s) left.`);

interface Claim {
  claim: string;
  where: string;
  confirmedBy: string | null;
}

const claims = JSON.parse(
  readFileSync(new URL("./claims.json", import.meta.url), "utf-8"),
) as Claim[];
const open = claims.filter((item) => item.confirmedBy === null || item.confirmedBy.trim() === "");

console.log("\nClaims waiting for sign-off by the owner and counsel:\n");
for (const item of open) console.log(`- ${item.claim}\n    ${item.where}`);
console.log(`\n${open.length} of ${claims.length} claim(s) not signed off.`);

if (process.argv.includes("--strict") && (found.size > 0 || open.length > 0)) process.exit(1);
