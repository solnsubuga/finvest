const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
(async () => {
  const b = await chromium.launch();
  const dir = path.join(__dirname, 'html'), out = path.join(__dirname, 'png');
  fs.mkdirSync(out, { recursive: true });
  for (const f of fs.readdirSync(dir).sort()) {
    const phone = f.includes('-phone-');
    const con = f.startsWith('console');
    const vp = phone ? { width: 390, height: 844 } : (con ? { width: 1440, height: 900 } : { width: 1280, height: 800 });
    const p = await b.newPage({ viewport: vp, deviceScaleFactor: 2 });
    await p.goto('file://' + path.join(dir, f));
    await p.evaluate(() => document.fonts.ready);
    await p.screenshot({ path: path.join(out, f.replace('.html', '.png')), fullPage: true });
    await p.close();
  }
  await b.close();
})();
