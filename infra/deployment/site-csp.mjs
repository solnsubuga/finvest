// Prints the website's Content-Security-Policy as a Caddy snippet, for site.Dockerfile.
//
//   node site-csp.mjs DIST_DIR API_ORIGIN > csp.caddy
//
// Scripts may come only from the site itself. Astro writes small scripts into the page, so each
// inline script found in the built pages is allowed by its SHA-256 hash and nothing else inline
// can run. The browser reads rates and quotes from the API, the only other host it may call.
// Cloudflare Web Analytics, which Cloudflare may add to pages it serves, is allowed as on the
// console.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const [dist, api] = process.argv.slice(2);
if (!dist || !/^https:\/\/[a-z0-9]([a-z0-9.-]*[a-z0-9])?$/.test(api ?? "")) {
  console.error("Usage: node site-csp.mjs DIST_DIR https://api.host");
  process.exit(1);
}

const pages = readdirSync(dist, { recursive: true })
  .filter((name) => name.endsWith(".html"))
  .map((name) => join(dist, name));
if (pages.length === 0) {
  console.error(`No pages in ${dist}`);
  process.exit(1);
}

const hashes = new Set();
for (const page of pages) {
  const html = readFileSync(page, "utf8");
  for (const [, attributes, body] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    if (/\ssrc\s*=/i.test(attributes) || body === "") continue;
    hashes.add(`'sha256-${createHash("sha256").update(body, "utf8").digest("base64")}'`);
  }
}

const policy = [
  "default-src 'self'",
  ["script-src 'self'", ...[...hashes].sort(), "https://static.cloudflareinsights.com"].join(" "),
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  `connect-src 'self' ${api} https://cloudflareinsights.com`,
  "object-src 'none'",
  "base-uri 'self'",
  // The contact form falls back to the visitor's own mail program.
  "form-action 'self' mailto:",
  "frame-ancestors 'none'",
].join("; ");
console.log(`header Content-Security-Policy "${policy}"`);
