// Off camera: draw the invented ID card photos and the selfie that Esther hands in. They are
// specimens, plainly marked, in no real document's design, for an invented person. Written to
// $WORK/documents as front.jpg, back.jpg and selfie.jpg (names that read well on screen).
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";
import { WORK } from "../lib/browser.mjs";
import { CLIENT } from "../lib/people.mjs";

const out = path.join(WORK, CLIENT.documents);
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

const face = `
<svg viewBox="0 0 200 240" xmlns="http://www.w3.org/2000/svg">
  <rect width="200" height="240" fill="#d9dccf"/>
  <ellipse cx="100" cy="250" rx="90" ry="70" fill="#2d4a6b"/>
  <rect x="84" y="150" width="32" height="40" fill="#6b4226"/>
  <ellipse cx="100" cy="112" rx="50" ry="60" fill="#7a4a2a"/>
  <path d="M50 100 Q52 40 100 42 Q150 40 150 100 Q140 70 100 66 Q60 70 50 100Z" fill="#1d1410"/>
  <ellipse cx="80" cy="112" rx="6" ry="4" fill="#1d1410"/><ellipse cx="120" cy="112" rx="6" ry="4" fill="#1d1410"/>
  <path d="M84 142 Q100 152 116 142" stroke="#3b2016" stroke-width="4" fill="none" stroke-linecap="round"/>
</svg>`;

const card = (inner) => `<!doctype html><html><head><style>
  body { margin: 0; width: 1600px; height: 1000px; background: radial-gradient(circle at 30% 30%, #3a3a3a, #161616);
    display: grid; place-items: center; font-family: "DejaVu Sans", sans-serif; }
  .card { width: 1180px; height: 800px; border-radius: 40px; background: linear-gradient(135deg, #f3efe2, #e4e9dc);
    box-shadow: 0 30px 60px rgba(0,0,0,.5); position: relative; overflow: hidden; transform: rotate(-1.5deg); }
  .band { height: 110px; background: #3d5a40; color: #fff; display: flex; align-items: center; padding: 0 50px;
    font-size: 40px; letter-spacing: 6px; font-weight: 700; }
  .stamp { position: absolute; right: -60px; top: 330px; transform: rotate(-28deg); font-size: 96px; font-weight: 800;
    color: rgba(180, 35, 24, .28); letter-spacing: 14px; }
  .row { font-size: 30px; color: #333; margin: 0 0 6px; } .val { font-size: 44px; font-weight: 700; color: #111; margin: 0 0 22px; }
</style></head><body><div class="card">${inner}<div class="stamp">SPECIMEN</div></div></body></html>`;

await page.setContent(card(`
  <div class="band">IDENTITY CARD · DEMO</div>
  <div style="display:flex; gap:50px; padding:50px">
    <div style="width:300px; height:420px; border-radius:16px; overflow:hidden; flex:none">${face}</div>
    <div>
      <p class="row">Surname</p><p class="val">${CLIENT.family.toUpperCase()}</p>
      <p class="row">Given name</p><p class="val">${CLIENT.given.toUpperCase()}</p>
      <p class="row">Date of birth</p><p class="val">${CLIENT.birth.day}.${CLIENT.birth.month}.${CLIENT.birth.year}</p>
      <p class="row">Expires</p><p class="val">${CLIENT.idExpires.replaceAll("/", ".")}</p>
      <p class="row">Card number</p><p class="val">${CLIENT.nin}</p>
    </div>
  </div>
  <div style="position:absolute; bottom:30px; left:50px; font-size:24px; color:#555">Invented for the Finvest product video. Not a real document.</div>`));
await page.screenshot({ path: path.join(out, "id-front.jpg"), quality: 90, type: "jpeg" });

await page.setContent(card(`
  <div class="band">IDENTITY CARD · DEMO · BACK</div>
  <div style="padding:60px 50px">
    <p class="row">Place of birth</p><p class="val">KAMPALA</p>
    <p class="row">Issued</p><p class="val">02.08.2019</p>
    <div style="margin-top:40px; height:120px; background:repeating-linear-gradient(90deg,#111 0 6px,transparent 6px 12px,#111 12px 15px,transparent 15px 22px)"></div>
    <p style="font: 34px monospace; margin-top:30px; color:#222">IDDEMO&lt;&lt;${CLIENT.family.toUpperCase()}&lt;&lt;${CLIENT.given.toUpperCase()}&lt;&lt;&lt;&lt;</p>
  </div>`));
await page.screenshot({ path: path.join(out, "id-back.jpg"), quality: 90, type: "jpeg" });

await page.setViewportSize({ width: 900, height: 1200 });
await page.setContent(`<!doctype html><body style="margin:0;background:#cfd4c6">${face.replace('viewBox="0 0 200 240"', 'viewBox="0 0 200 240" width="900" height="1200" preserveAspectRatio="xMidYMid slice"')}</body>`);
await page.screenshot({ path: path.join(out, "selfie.jpg"), quality: 90, type: "jpeg" });
await browser.close();
console.log("Documents in", out);
