// Prints the client web app's Content-Security-Policy as a Caddy snippet, for app.Dockerfile.
//
//   node app-csp.mjs DIST_DIR API_ORIGIN [REGULA_FACE_URL] > csp.caddy
//
// Scripts come from the app itself; any inline script Expo writes into index.html is allowed by
// its SHA-256 hash and nothing else inline can run. react-native-web writes its styles into the
// page at run time, so styles may be inline. The guided selfie (docs/identity-checks.md) runs
// MediaPipe's face detector as WebAssembly from /face ('wasm-unsafe-eval', still no eval of
// script), and Regula's liveness component, loaded from /regula, fetches its WebAssembly worker
// from wasm.regulaforensics.com and talks to Regula's Face service. Photos the app has just taken
// show from blob: and data: addresses. Cloudflare Web Analytics, which Cloudflare may add to
// pages it serves, is allowed as on the console and the website.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const [dist, api, regulaFace = ""] = process.argv.slice(2);
const ORIGIN = /^https:\/\/[a-z0-9]([a-z0-9.-]*[a-z0-9])?$/;
if (!dist || !ORIGIN.test(api ?? "")) {
  console.error("Usage: node app-csp.mjs DIST_DIR https://api.host [https://regula.face/address]");
  process.exit(1);
}
let regulaOrigin = "";
if (regulaFace !== "") {
  const url = new URL(regulaFace);
  if (url.protocol !== "https:" || !ORIGIN.test(`https://${url.hostname}`)) {
    console.error("The Regula Face address must be https");
    process.exit(1);
  }
  regulaOrigin = url.origin;
}

const html = readFileSync(join(dist, "index.html"), "utf8");
const hashes = new Set();
for (const [, attributes, body] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
  if (/\ssrc\s*=/i.test(attributes) || body === "") continue;
  hashes.add(`'sha256-${createHash("sha256").update(body, "utf8").digest("base64")}'`);
}

const REGULA_WASM = "https://wasm.regulaforensics.com";
const policy = [
  "default-src 'self'",
  ["script-src 'self' 'wasm-unsafe-eval'", ...[...hashes].sort(), REGULA_WASM, "https://static.cloudflareinsights.com"].join(" "),
  // MediaPipe and Regula start their workers from blob: addresses as well as their own files.
  `worker-src 'self' blob: ${REGULA_WASM}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "media-src 'self' blob:",
  "font-src 'self' data:",
  ["connect-src 'self'", api, REGULA_WASM, regulaOrigin, "https://cloudflareinsights.com"].filter(Boolean).join(" "),
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");
console.log(`header Content-Security-Policy "${policy}"`);
