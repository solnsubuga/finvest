/**
 * Checks on the pages as they will be served. Run `pnpm build` first, then `pnpm test:build`.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const dist = new URL("../dist/", import.meta.url).pathname;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

const all = existsSync(dist) ? files(dist) : [];
const pages = all.filter((path) => path.endsWith(".html"));
const read = (path: string) => readFileSync(path, "utf-8");
const name = (path: string) => relative(dist, path);

function resolves(href: string): boolean {
  const path = href.split("#")[0]?.split("?")[0] ?? "";
  if (path === "" || path === "/") return true;
  const target = join(dist, path);
  return existsSync(target) && statSync(target).isFile()
    ? true
    : existsSync(join(target, "index.html"));
}

describe("the built site", () => {
  it("has been built", () => {
    expect(pages.length, "run `pnpm build` before `pnpm test:build`").toBeGreaterThan(0);
  });

  it("never mentions the back office", () => {
    for (const path of all.filter((p) => /\.(html|js|css|txt|xml|json)$/.test(p))) {
      expect(read(path).toLowerCase(), name(path)).not.toMatch(/admin[.-]|\/admin\b/);
    }
  });

  it("shows no placeholder", () => {
    // A fact Finvest has not supplied is left out (src/data/company.ts), never bracketed.
    for (const path of pages) {
      const text = read(path).replace(/<script[\s\S]*?<\/script>/g, "");
      expect(text, name(path)).not.toMatch(/\[[A-Z][A-Z :,'.a-z]*[A-Z.]\]/);
      expect(text, name(path)).not.toMatch(/lorem ipsum|\bTBC\b|\bTBD\b|\bTODO\b/i);
    }
  });

  it("has no inline styles, so the content security policy can refuse them", () => {
    for (const path of pages) {
      const text = read(path);
      expect(text, name(path)).not.toMatch(/<[^>]+\sstyle=/);
      expect(text, name(path)).not.toMatch(/<style\b/);
    }
  });

  it("carries no rate before tax and no tax rate", () => {
    for (const path of all.filter((p) => /\.(html|js)$/.test(p))) {
      const text = read(path);
      expect(text, name(path)).not.toMatch(/annual_rate|tax_bps|tax_rate|12\.50?%|13\.75%|7\.25%/);
    }
  });

  it("links only to pages that exist, the client app, or an email address", () => {
    for (const path of pages) {
      const hrefs = [...read(path).matchAll(/<a\b[^>]*\bhref="([^"]*)"/g)].map((m) => m[1] ?? "");
      expect(hrefs.length, name(path)).toBeGreaterThan(0);
      for (const href of hrefs) {
        if (href.startsWith("/")) {
          expect(resolves(href), `${name(path)} links to ${href}`).toBe(true);
        } else if (href.startsWith("#")) {
          const id = href.slice(1);
          expect(read(path), `${name(path)} links to ${href}`).toContain(`id="${id}"`);
        } else {
          expect(href, name(path)).toMatch(
            /^(https:\/\/app(-staging)?\.finvestug\.com(\/[a-z-]*)?|mailto:[a-z]+@finvestug\.com|tel:\+?[0-9]+)$/,
          );
        }
      }
    }
  });

  it("points in-page links at sections that exist", () => {
    for (const path of pages) {
      for (const match of read(path).matchAll(/href="(\/[^"#]*)#([^"]+)"/g)) {
        const page = match[1] === "/" ? "index.html" : join(match[1] ?? "", "index.html");
        expect(read(join(dist, page)), `${name(path)} links to ${match[0]}`).toContain(
          `id="${match[2]}"`,
        );
      }
    }
  });

  it("gives every page one h1, a title and a description", () => {
    for (const path of pages) {
      const html = read(path);
      expect(html.match(/<h1[\s>]/g)?.length, name(path)).toBe(1);
      expect(html, name(path)).toMatch(/<title>[^<]{10,70}<\/title>/);
      expect(html, name(path)).toMatch(/<meta name="description" content="[^"]{40,170}"/);
    }
  });

  it("stays out of search engines unless the build says it is ready", () => {
    const ready = process.env["PUBLIC_INDEXABLE"] === "true";
    for (const path of pages) {
      expect(read(path).includes('content="noindex, nofollow"'), name(path)).toBe(!ready);
    }
    expect(read(join(dist, "robots.txt"))).toContain(ready ? "Allow: /" : "Disallow: /");
  });

  it("loads nothing from another site", () => {
    for (const path of pages) {
      const sources = [...read(path).matchAll(/<(?:script|link|img)\b[^>]*\b(?:src|href)="([^"]*)"/g)]
        .map((m) => m[1] ?? "")
        .filter((src) => /^(https?:)?\/\//.test(src));
      // The canonical link names the site's own address.
      expect(sources.filter((src) => !/^https:\/\/(staging\.)?finvestug\.com/.test(src)), name(path)).toEqual([]);
    }
  });
});
