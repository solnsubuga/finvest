/**
 * Lists every [BRACKETED] placeholder still on the built pages: facts the site is waiting for,
 * such as the licence number. Run `pnpm build` first. With --strict it fails if any are left,
 * which is the check to run before the site is opened to search engines.
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
if (process.argv.includes("--strict") && found.size > 0) process.exit(1);
