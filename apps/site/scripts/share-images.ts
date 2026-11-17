/**
 * Draws the link preview pictures in public/share/ from src/data/share.ts.
 *
 *   pnpm share-images
 *
 * Each picture is 1200 by 630, the size WhatsApp, Slack, X, LinkedIn and Facebook all show in
 * full, on brand black with the site's own font, mark and colours. Pictures are committed, so the
 * site build needs no browser. Run this after changing the words in src/data/share.ts, and check
 * the result with `pnpm test:build`, which fails on a picture of the wrong size or over 300 KB
 * (WhatsApp leaves out a larger one).
 *
 * Uses Playwright's Chromium. Set PLAYWRIGHT_CHROMIUM to a browser binary to use another one.
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { colors } from "@finvest/tokens";
import { type ShareImage, shareImageHeight, shareImages, shareImageWidth, shareTagline } from "../src/data/share.ts";

const require = createRequire(import.meta.url);
const { chromium } = require("@playwright/test") as typeof import("@playwright/test");

const font = readFileSync(
  require.resolve("@fontsource-variable/plus-jakarta-sans/files/plus-jakarta-sans-latin-wght-normal.woff2"),
).toString("base64");

const out = new URL("../public/share/", import.meta.url);
mkdirSync(out, { recursive: true });

const escape = (text: string) =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** The mark from src/components/Logo.astro. */
const mark = (size: number, tile: string) => `
  <svg width="${size}" height="${size}" viewBox="0 0 64 64" aria-hidden="true">
    <rect width="64" height="64" rx="18" fill="${tile}" />
    <path d="M14 44l11-12 9 8 9-11" fill="none" stroke="${colors.white}" stroke-width="7"
      stroke-linecap="round" stroke-linejoin="round" />
    <circle cx="49" cy="20" r="6.5" fill="${colors.lime}" />
  </svg>`;

function page(image: ShareImage): string {
  const words = image.headline.length + (image.highlight?.length ?? 0);
  // Longer headings get a smaller size so each fits in three lines.
  const size = image.eyebrow === null ? 100 : words > 50 ? 66 : words > 34 ? 72 : 84;
  return `<!doctype html>
<html><head><meta charset="utf-8" /><style>
  @font-face {
    font-family: "Jakarta";
    font-weight: 200 800;
    src: url(data:font/woff2;base64,${font}) format("woff2");
  }
  * { box-sizing: border-box; margin: 0; }
  html, body { width: ${shareImageWidth}px; height: ${shareImageHeight}px; }
  body {
    position: relative;
    overflow: hidden;
    background: ${colors.black};
    color: ${colors.white};
    font-family: "Jakarta", sans-serif;
  }
  .card {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    padding: 64px 72px 60px;
  }
  .brand { display: flex; align-items: center; gap: 16px; font-size: 38px; font-weight: 800; letter-spacing: -0.03em; }
  .body { margin-top: auto; max-width: 860px; }
  .eyebrow {
    margin-bottom: 20px;
    color: ${colors.lime};
    font-size: 24px;
    font-weight: 700;
    letter-spacing: 1.6px;
    text-transform: uppercase;
  }
  h1 { font-size: ${size}px; line-height: 1.02; font-weight: 800; letter-spacing: -0.04em; }
  h1 span { color: ${colors.lime}; white-space: nowrap; }
  .foot {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 32px;
    margin-top: 40px;
    padding-top: 28px;
    border-top: 2px solid ${colors.onBlackDivider};
    color: ${colors.onBlackMuted};
    font-size: 26px;
    font-weight: 500;
  }
  .foot strong { color: ${colors.white}; font-weight: 700; }
  .line { position: absolute; top: 40px; right: -40px; opacity: 0.9; }
</style></head>
<body>
  <svg class="line" width="520" height="360" viewBox="0 0 520 360" aria-hidden="true">
    <path d="M40 300l140-150 110 100 150-170" fill="none" stroke="${colors.onBlackSurface}" stroke-width="44"
      stroke-linecap="round" stroke-linejoin="round" />
    <circle cx="470" cy="70" r="36" fill="${colors.lime}" />
  </svg>
  <div class="card">
    <div class="brand">${mark(60, colors.onBlackSurface)}Finvest</div>
    <div class="body">
      ${image.eyebrow ? `<p class="eyebrow">${escape(image.eyebrow)}</p>` : ""}
      <h1>${escape(image.headline)}${image.highlight ? ` <span>${escape(image.highlight)}</span>` : ""}</h1>
    </div>
    <div class="foot"><span>${escape(shareTagline)}</span><strong>finvestug.com</strong></div>
  </div>
</body></html>`;
}

const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM } : {},
);
try {
  const tab = await browser.newPage({
    viewport: { width: shareImageWidth, height: shareImageHeight },
    deviceScaleFactor: 1,
  });
  for (const image of Object.values(shareImages)) {
    await tab.setContent(page(image));
    await tab.evaluate(() => document.fonts.ready);
    const png = await tab.screenshot({ type: "png" });
    writeFileSync(new URL(`${image.name}.png`, out), png);
    console.log(`public/share/${image.name}.png ${Math.round(png.length / 1024)} KB`);
  }
} finally {
  await browser.close();
}
