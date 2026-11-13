/**
 * What the site still needs before launch. Run `pnpm build` first.
 *
 * 1. Any [BRACKETED] placeholder on the built pages. There should be none: a fact Finvest
 *    has not supplied is left out (src/data/company.ts), and `pnpm test:build` fails on one.
 * 2. Every claim in claims.json that nobody has signed off: statements written out in full
 *    that the owner and counsel must confirm are true. To sign one off, set its `confirmedBy`
 *    to who confirmed it and when.
 *
 * 3. Every fact in src/data/company.ts that must be on the site before launch (registered
 *    company name, regulator, licence number) and is still empty. The pages leave a missing
 *    fact out, so only this list shows it.
 *
 * With --strict it fails if anything is left in any of the three lists. Run that before the site is
 * opened to search engines.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { company } from "../src/data/company.ts";
import { missingFacts } from "../src/lib/launch.ts";

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

const missing = missingFacts(company);
console.log("\nFacts needed before launch, to be entered in src/data/company.ts:\n");
for (const key of missing) console.log(`- ${key}`);
console.log(`\n${missing.length} required fact(s) missing.`);

if (process.argv.includes("--strict") && (found.size > 0 || open.length > 0 || missing.length > 0)) {
  process.exit(1);
}
