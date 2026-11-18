// The still pictures the edit lays over the recordings: the window frame with its address bar,
// the phone frame, the caption pills, the chapter title cards and the outro. Drawn as HTML in
// the brand's colours and font from @finvest/tokens, and saved as PNGs.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { chromium } from "playwright";

const require = createRequire(import.meta.url);
const tokensCss = fs.readFileSync(require.resolve("@finvest/tokens/tokens.css"), "utf8");
const fontFile = require.resolve("@fontsource-variable/plus-jakarta-sans/files/plus-jakarta-sans-latin-wght-normal.woff2");

export const W = 1920;
export const H = 1080;

// Where the recordings sit in the 1920x1080 frame.
export const WIDE = { x: 240, y: 72, w: 1440, h: 810, bar: 44 };
export const PHONE = { x: 765, y: 34, w: 390, h: 760, status: 40, url: 48, bezel: 12 };
export const CAPTION_Y = 952;

const LOGO = `<svg viewBox="0 0 64 64" width="100%" height="100%"><rect width="64" height="64" rx="15" fill="#0D0D0D"/><path d="M14 44l11-12 9 8 9-11" fill="none" stroke="#FFFFFF" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/><circle cx="49" cy="20" r="6.5" fill="#B6EB0A"/></svg>`;

const base = `
  ${tokensCss}
  @font-face { font-family: "Plus Jakarta Sans"; src: url("data:font/woff2;base64,${fs.readFileSync(fontFile).toString("base64")}") format("woff2"); font-weight: 200 800; }
  * { box-sizing: border-box; margin: 0; }
  html, body { width: ${W}px; height: ${H}px; background: transparent; font-family: var(--fv-font-family); }
  .stage { position: absolute; inset: 0; background:
      radial-gradient(1200px 700px at 50% 35%, #1d1f1a 0%, #0b0b0b 70%); }
  .lock { width: 14px; height: 14px; }
`;

const escape = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const lockSvg = `<svg class="lock" viewBox="0 0 16 16"><rect x="3" y="7" width="10" height="7" rx="2" fill="currentColor"/><path d="M5 7V5a3 3 0 0 1 6 0v2" stroke="currentColor" stroke-width="1.8" fill="none"/></svg>`;

function wideFrame(host) {
  const f = WIDE;
  return `<div class="stage"></div>
  <div style="position:absolute; left:${f.x}px; top:${f.y - f.bar}px; width:${f.w}px; height:${f.h + f.bar}px; border-radius:14px;
       background:#fff; box-shadow: 0 30px 80px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.08); overflow:hidden">
    <div style="height:${f.bar}px; background:#ecece8; display:flex; align-items:center; padding:0 16px; gap:8px; border-bottom:1px solid #d9dbd3">
      <span style="width:12px;height:12px;border-radius:50%;background:#ff5f57"></span>
      <span style="width:12px;height:12px;border-radius:50%;background:#febc2e"></span>
      <span style="width:12px;height:12px;border-radius:50%;background:#28c840"></span>
      <div style="margin-left:120px; flex:1; max-width:760px; height:30px; border-radius:999px; background:#fff; display:flex; align-items:center; gap:8px; padding:0 16px; color:#5e5e5a; font-size:15px; font-weight:500; border:1px solid #dcded6">
        ${lockSvg}<span style="color:#141414">${escape(host)}</span>
      </div>
    </div>
    <div style="height:${f.h}px; background:#111"></div>
  </div>`;
}

function phoneFrame(host, side) {
  const p = PHONE;
  const screenH = p.status + p.url + p.h + 22;
  return `<div class="stage"></div>
  ${side ? `<div style="position:absolute; left:150px; top:360px; width:520px; color:#fff">
      <div style="font-size:20px; font-weight:700; color:var(--fv-color-lime); letter-spacing:.08em; text-transform:uppercase">${escape(side.kicker)}</div>
      <div style="font-size:52px; font-weight:800; line-height:1.08; margin-top:14px">${escape(side.title)}</div>
      <div style="font-size:24px; color:#c9c9c4; margin-top:18px; line-height:1.35">${escape(side.subtitle)}</div>
    </div>` : ""}
  <div style="position:absolute; left:${p.x - p.bezel}px; top:${p.y - p.bezel}px; width:${p.w + 2 * p.bezel}px; height:${screenH + 2 * p.bezel}px;
       border-radius:58px; background:#1b1b1b; box-shadow: 0 30px 80px rgba(0,0,0,.6), inset 0 0 0 2px #3a3a3a"></div>
  <div style="position:absolute; left:${p.x}px; top:${p.y}px; width:${p.w}px; height:${screenH}px; border-radius:46px; overflow:hidden; background:#f4f5f0">
    <div style="height:${p.status}px; display:flex; align-items:center; justify-content:space-between; padding:6px 30px 0; font-size:15px; font-weight:700; color:#141414">
      <span>9:41</span>
      <span style="width:110px; height:28px; border-radius:20px; background:#0d0d0d"></span>
      <span style="display:flex; gap:5px; align-items:center">
        <svg width="18" height="12" viewBox="0 0 18 12"><rect x="0" y="8" width="3" height="4" rx="1" fill="#141414"/><rect x="5" y="5" width="3" height="7" rx="1" fill="#141414"/><rect x="10" y="2" width="3" height="10" rx="1" fill="#141414"/><rect x="15" y="0" width="3" height="12" rx="1" fill="#141414"/></svg>
        <svg width="26" height="12" viewBox="0 0 26 12"><rect x=".5" y=".5" width="22" height="11" rx="3" stroke="#141414" fill="none"/><rect x="2" y="2" width="17" height="8" rx="2" fill="#141414"/><rect x="23.5" y="4" width="2" height="4" rx="1" fill="#141414"/></svg>
      </span>
    </div>
    <div style="height:${p.url}px; display:flex; align-items:center; padding:0 14px">
      <div style="flex:1; height:36px; border-radius:12px; background:#e4e6de; display:flex; align-items:center; justify-content:center; gap:6px; color:#141414; font-size:15px; font-weight:600">${lockSvg}${escape(host)}</div>
    </div>
    <div style="height:${p.h}px; background:#111"></div>
    <div style="height:22px; display:flex; justify-content:center; align-items:center"><span style="width:130px;height:5px;border-radius:3px;background:#141414"></span></div>
  </div>`;
}

function caption(text, n, of) {
  return `<div style="position:absolute; left:0; right:0; top:0; display:flex; flex-direction:column; align-items:center">
    <div style="display:flex; align-items:center; gap:18px; max-width:1500px; padding:16px 30px 16px 18px; border-radius:999px;
         background:rgba(13,13,13,.92); box-shadow: 0 10px 30px rgba(0,0,0,.35), 0 0 0 1px rgba(255,255,255,.10); color:#fff">
      <span style="flex:none; font-size:16px; font-weight:700; color:#0d0d0d; background:var(--fv-color-lime); border-radius:999px; padding:6px 12px; white-space:nowrap">Step ${n} of ${of}</span>
      <span style="font-size:31px; font-weight:600; letter-spacing:-.01em; white-space:nowrap">${escape(text)}</span>
    </div>
    <div style="margin-top:10px; width:360px; height:4px; border-radius:2px; background:rgba(255,255,255,.18); overflow:hidden">
      <div style="width:${Math.round((n / of) * 100)}%; height:100%; background:var(--fv-color-lime)"></div>
    </div>
  </div>`;
}

function titleCard(kicker, title, subtitle) {
  return `<div style="position:absolute; inset:0; background:var(--fv-color-black)">
    <div style="position:absolute; right:-220px; top:-220px; width:900px; height:900px; border-radius:50%;
         background: radial-gradient(circle, rgba(182,235,10,.22), rgba(182,235,10,0) 65%)"></div>
    <div style="position:absolute; left:180px; top:300px; color:#fff">
      <div style="display:flex; align-items:center; gap:18px">
        <div style="width:72px; height:72px; border-radius:18px; box-shadow:0 0 0 2px #2a2a2a">${LOGO}</div>
        <div style="font-size:44px; font-weight:800; letter-spacing:-.02em">finvest</div>
      </div>
      <div style="margin-top:70px; font-size:26px; font-weight:700; color:var(--fv-color-lime); letter-spacing:.1em; text-transform:uppercase">${escape(kicker)}</div>
      <div style="margin-top:16px; font-size:96px; font-weight:800; letter-spacing:-.03em; line-height:1.02; max-width:1500px">${escape(title)}</div>
      <div style="margin-top:28px; font-size:36px; color:#c9c9c4; max-width:1400px">${escape(subtitle)}</div>
    </div>
  </div>`;
}

function outro(site) {
  return `<div style="position:absolute; inset:0; background:var(--fv-color-black); display:flex; flex-direction:column; align-items:center; justify-content:center; color:#fff">
    <div style="position:absolute; left:50%; top:50%; width:1100px; height:1100px; margin:-550px 0 0 -550px; border-radius:50%;
         background: radial-gradient(circle, rgba(182,235,10,.16), rgba(182,235,10,0) 60%)"></div>
    <div style="display:flex; align-items:center; gap:26px; position:relative">
      <div style="width:120px; height:120px; border-radius:30px; box-shadow:0 0 0 2px #2a2a2a">${LOGO}</div>
      <div style="font-size:96px; font-weight:800; letter-spacing:-.03em">finvest</div>
    </div>
    <div style="margin-top:44px; font-size:36px; color:#c9c9c4; position:relative">Treasury bills and bonds, on your phone.</div>
    <div style="margin-top:60px; font-size:44px; font-weight:700; color:var(--fv-color-lime); position:relative">${escape(site)}</div>
  </div>`;
}

let browser;
// Headless Chromium sometimes stops capturing after a few large pages ("Unable to capture
// screenshot"); a fresh browser draws the card fine, so retry once with one.
async function render(html, file, options = {}) {
  try {
    return await draw(html, file, options);
  } catch (error) {
    if (!/Unable to capture screenshot/.test(String(error))) throw error;
    await browser?.close().catch(() => {});
    browser = undefined;
    return await draw(html, file, options);
  }
}

async function draw(html, file, { height = H, transparent = true } = {}) {
  browser ??= await chromium.launch();
  const page = await browser.newPage({ viewport: { width: W, height } });
  await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>${base} html,body{height:${height}px}</style></head><body>${html}</body></html>`);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: file, omitBackground: transparent });
  await page.close();
  return file;
}

export const cards = {
  wideFrame: (host, file) => render(wideFrame(host), file, { transparent: false }),
  phoneFrame: (host, side, file) => render(phoneFrame(host, side), file, { transparent: false }),
  caption: (text, n, of, file) => render(caption(text, n, of), file, { height: 110 }),
  title: (kicker, title, subtitle, file) => render(titleCard(kicker, title, subtitle), file, { transparent: false }),
  outro: (site, file) => render(outro(site), file, { transparent: false }),
  async close() {
    await browser?.close();
    browser = undefined;
  },
};
