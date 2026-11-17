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

  it("keeps the PDF of each version of the terms and the privacy notice", () => {
    for (const slug of ["terms", "privacy"]) {
      const versioned = all.filter((path) => new RegExp(`legal/${slug}-\\d{4}-\\d{2}-\\d{2}\\.pdf$`).test(path));
      expect(versioned.length, slug).toBeGreaterThan(0);
    }
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

  it("links only to pages that exist, the client app, its store listings, or an email address", () => {
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
            /^(https:\/\/app(-staging)?\.finvestug\.com(\/[a-z-]*)?|https:\/\/(apps\.apple\.com|play\.google\.com)\/[^"\s]+|mailto:[a-z]+@finvestug\.com|tel:\+?[0-9]+)$/,
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

  it("gives every page a link preview that apps can read without running a script", () => {
    const site = (process.env["PUBLIC_SITE_URL"] ?? "https://finvestug.com").replace(/\/+$/, "");
    const meta = (html: string, key: string) =>
      html.match(new RegExp(`<meta (?:property|name)="${key}" content="([^"]*)"`))?.[1];
    for (const path of pages) {
      const html = read(path);
      for (const key of ["og:title", "og:description", "og:url", "og:image:alt", "twitter:title", "twitter:description"]) {
        expect(meta(html, key), `${name(path)} ${key}`).toBeTruthy();
      }
      expect(meta(html, "twitter:card"), name(path)).toBe("summary_large_image");
      const image = meta(html, "og:image") ?? "";
      expect(image, name(path)).toMatch(new RegExp(`^${site.replace(/[.]/g, "\\.")}/share/[a-z-]+\\.png$`));
      expect(meta(html, "twitter:image"), name(path)).toBe(image);
      expect(meta(html, "og:image:width"), name(path)).toBe("1200");
      expect(meta(html, "og:image:height"), name(path)).toBe("630");
      // The picture is in the build, 1200 by 630 as the tags say, and small enough for WhatsApp,
      // which leaves out a picture over about 300 KB.
      const file = join(dist, new URL(image).pathname);
      expect(existsSync(file), `${name(path)} shares ${image}`).toBe(true);
      const png = readFileSync(file);
      expect(png.subarray(1, 4).toString("ascii"), image).toBe("PNG");
      expect([png.readUInt32BE(16), png.readUInt32BE(20)], image).toEqual([1200, 630]);
      expect(png.length, image).toBeLessThan(300 * 1024);
    }
  });

  it("lets the apps that draw link previews read the pages before launch", () => {
    if (process.env["PUBLIC_INDEXABLE"] === "true") return;
    const robots = read(join(dist, "robots.txt"));
    for (const agent of ["facebookexternalhit", "WhatsApp", "Twitterbot", "LinkedInBot", "Slackbot-LinkExpanding"]) {
      expect(robots, agent).toMatch(new RegExp(`User-agent: ${agent}\\nAllow: /\\n`));
    }
    expect(robots).toMatch(/User-agent: \*\nDisallow: \/\n$/);
  });

  it("tells search engines the site is for Uganda, in English", () => {
    for (const path of pages) {
      expect(read(path), name(path)).toContain('<html lang="en-UG"');
    }
  });

  it("lists every page in the sitemap, and only pages that exist", () => {
    const site = (process.env["PUBLIC_SITE_URL"] ?? "https://finvestug.com").replace(/\/+$/, "");
    const listed = [...read(join(dist, "sitemap.xml")).matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1] ?? "");
    const expected = pages
      .map((path) => `/${name(path)}`.replace(/index\.html$/, ""))
      .filter((path) => path !== "/404.html")
      .map((path) => `${site}${path}`)
      .sort();
    expect([...listed].sort()).toEqual(expected);
  });

  it("gives search engines structured data that parses and says only what the page says", () => {
    const blocks = (html: string) =>
      [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map(
        (m) => JSON.parse(m[1] ?? "") as Record<string, unknown>,
      );
    const home = blocks(read(join(dist, "index.html")));
    expect(home.map((data) => data["@type"])).toEqual(["FinancialService", "WebSite", "FAQPage"]);
    const help = read(join(dist, "help", "index.html"));
    const faq = blocks(help)[0] as { mainEntity: { name: string; acceptedAnswer: { text: string } }[] };
    expect(faq.mainEntity.length).toBeGreaterThan(5);
    for (const item of faq.mainEntity) {
      // Each answer is on the page, word for word (quotes and ampersands are escaped in the HTML).
      expect(help.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"), item.name).toContain(item.acceptedAnswer.text);
    }
  });
});
