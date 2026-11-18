// One take of one chapter: a Playwright page with a visible cursor, recorded frame by frame
// through the DevTools screencast, plus a log of what happened when (captions, clicks, zooms,
// cuts and the address shown). edit.mjs turns the frames and the log into the chapter's video.
//
// In a dry run (PV_DRY=1) nothing is recorded: each step saves a screenshot instead, to check a
// script quickly.
import fs from "node:fs";
import { PRIVACY_SCRIPT } from "./privacy.mjs";
import path from "node:path";
import { WORK } from "./browser.mjs";

const DRY = process.env.PV_DRY === "1";

// The cursor and the click ring, drawn by the page itself so the recording shows them. They
// never take a click: pointer-events are off.
const CURSOR_SCRIPT = `
(() => {
  if (window.__pv) return;
  const style = \`
    #__pv_cursor { position: fixed; left: 0; top: 0; width: 26px; height: 26px; z-index: 2147483647;
      pointer-events: none; transform: translate(-200px, -200px); will-change: transform; }
    #__pv_cursor svg { filter: drop-shadow(0 2px 3px rgba(0,0,0,.35)); }
    #__pv_cursor.touch { width: 44px; height: 44px; }
    #__pv_cursor.touch .dot { position: absolute; inset: 0; border-radius: 50%;
      background: rgba(20,20,20,.28); border: 2px solid rgba(255,255,255,.9); box-shadow: 0 2px 8px rgba(0,0,0,.25); }
    .__pv_ring { position: fixed; z-index: 2147483646; pointer-events: none; width: 44px; height: 44px;
      margin: -22px 0 0 -22px; border-radius: 50%; border: 3px solid #B6EB0A; background: rgba(182,235,10,.18);
      animation: __pv_ring .55s ease-out forwards; }
    @keyframes __pv_ring { from { transform: scale(.35); opacity: 1 } to { transform: scale(1.35); opacity: 0 } }
  \`;
  const arrow = '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M3 2 L3 21 L8.2 16.4 L11.6 24 L15 22.5 L11.7 15 L19 15 Z" fill="#141414" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  let el, x = -200, y = -200, touch = false, anim = null;
  function mount() {
    if (el && document.documentElement.contains(el)) return;
    const s = document.createElement("style"); s.textContent = style; document.documentElement.appendChild(s);
    el = document.createElement("div"); el.id = "__pv_cursor";
    el.innerHTML = touch ? '<div class="dot"></div>' : arrow;
    if (touch) el.classList.add("touch");
    document.documentElement.appendChild(el);
    place(x, y);
  }
  function place(nx, ny) {
    x = nx; y = ny;
    if (!el) return;
    const off = touch ? 22 : 3;
    el.style.transform = "translate(" + (x - off) + "px," + (y - (touch ? 22 : 2)) + "px)";
  }
  const ease = (t) => (t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  window.__pv = {
    mode(t) { touch = t; if (el) { el.remove(); el = null; } mount(); },
    place(nx, ny) { mount(); place(nx, ny); },
    move(nx, ny, ms) {
      mount();
      if (anim) cancelAnimationFrame(anim);
      const sx = x, sy = y, start = performance.now();
      // A gentle arc, like a hand, not a ruler.
      const bend = Math.min(60, Math.hypot(nx - sx, ny - sy) * 0.12);
      return new Promise((done) => {
        const step = (now) => {
          const t = Math.min(1, (now - start) / ms), e = ease(t);
          const px = sx + (nx - sx) * e, py = sy + (ny - sy) * e - Math.sin(Math.PI * e) * bend;
          place(px, py);
          if (t < 1) anim = requestAnimationFrame(step); else { anim = null; done(); }
        };
        anim = requestAnimationFrame(step);
      });
    },
    ring() {
      const r = document.createElement("div"); r.className = "__pv_ring";
      r.style.left = x + "px"; r.style.top = y + "px";
      document.documentElement.appendChild(r); setTimeout(() => r.remove(), 700);
    },
    where() { return [x, y]; },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
`;

export class Take {
  /**
   * @param {import("playwright").Page} page
   * @param {{ chapter: object, name?: string, layout: "wide"|"phone"|"mail" }} options
   */
  constructor(page, { chapter, name = "main", layout }) {
    this.page = page;
    this.chapter = chapter;
    this.layout = layout;
    this.touch = layout === "phone";
    this.dir = path.join(WORK, "takes", `${chapter.id}-${name}`);
    this.events = [];
    this.frames = [];
    this.cursor = layout === "phone" ? [195, 600] : [900, 500];
    this.shot = 0;
  }

  static async prepare(context) {
    await context.addInitScript(CURSOR_SCRIPT);
    await context.addInitScript(PRIVACY_SCRIPT);
  }

  now() {
    return Date.now() / 1000;
  }

  log(type, data = {}) {
    this.events.push({ t: this.now(), type, ...data });
  }

  async start() {
    fs.rmSync(this.dir, { recursive: true, force: true });
    fs.mkdirSync(path.join(this.dir, "frames"), { recursive: true });
    await this.page.evaluate((t) => window.__pv?.mode(t), this.touch).catch(() => {});
    await this.page.evaluate(([x, y]) => window.__pv?.place(x, y), this.cursor).catch(() => {});
    this.page.on("framenavigated", (frame) => {
      if (frame === this.page.mainFrame()) {
        this.log("address", { url: frame.url() });
        // A new document has a new cursor: put it back where it was.
        frame.evaluate(([t, x, y]) => { window.__pv?.mode(t); window.__pv?.place(x, y); }, [this.touch, ...this.cursor]).catch(() => {});
      }
    });
    this.log("address", { url: this.page.url() });
    this.log("start");
    if (DRY) return;
    const vp = this.page.viewportSize();
    const scale = this.layout === "phone" ? 2 : 1;
    this.cdp = await this.page.context().newCDPSession(this.page);
    let n = 0;
    this.cdp.on("Page.screencastFrame", async ({ data, metadata, sessionId }) => {
      const file = `f${String(n++).padStart(6, "0")}.jpg`;
      fs.writeFileSync(path.join(this.dir, "frames", file), Buffer.from(data, "base64"));
      this.frames.push({ t: metadata.timestamp, file });
      await this.cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
    });
    await this.cdp.send("Page.startScreencast", {
      format: "jpeg",
      quality: 92,
      maxWidth: vp.width * scale,
      maxHeight: vp.height * scale,
      everyNthFrame: 1,
    });
    // The screencast sends a frame only when something changes; nudge one out now.
    await this.page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r())));
    await this.wait(400);
  }

  async stop() {
    await this.wait(600);
    this.log("end");
    // The blur must never stop quietly: say so, so the take can be recorded again.
    const blurError = await this.page.evaluate(() => window.__pvPrivacyError).catch(() => null);
    if (blurError) console.log(`  privacy blur error in ${this.chapter.id}: ${blurError.split("\n")[0]}`);
    if (this.cdp) {
      await this.cdp.send("Page.stopScreencast").catch(() => {});
      // Not detached: detaching a session switches off the page's virtual authenticator, and
      // the passkeys made in the take would be lost.
      this.cdp.removeAllListeners("Page.screencastFrame");
    }
    fs.mkdirSync(this.dir, { recursive: true });
    fs.writeFileSync(
      path.join(this.dir, "take.json"),
      JSON.stringify(
        {
          chapter: this.chapter.id,
          layout: this.layout,
          viewport: this.page.viewportSize(),
          captions: this.chapter.steps.map((s) => s.caption),
          frames: this.frames,
          events: this.events,
        },
        null,
        1,
      ),
    );
  }

  wait(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async snap(label) {
    if (!DRY) return;
    fs.mkdirSync(this.dir, { recursive: true });
    const file = path.join(this.dir, `${String(++this.shot).padStart(2, "0")}-${label.replace(/[^a-z0-9]+/gi, "-").slice(0, 40)}.png`);
    await this.page.screenshot({ path: file }).catch(() => {});
  }

  /** Show the next caption. The text must be the chapter's next step, spelled exactly. */
  async step(caption, { hold = 1400 } = {}) {
    this.chapter.stepIndex ??= 0;
    const expected = this.chapter.steps[this.chapter.stepIndex]?.caption;
    if (caption !== expected) throw new Error(`Step ${this.chapter.stepIndex + 1} of ${this.chapter.id} is "${expected}", not "${caption}"`);
    this.chapter.stepIndex += 1;
    this.log("caption", { n: this.chapter.stepIndex, of: this.chapter.steps.length, text: caption });
    if (DRY) console.log(`  [${this.chapter.id} ${this.chapter.stepIndex}/${this.chapter.steps.length}] ${caption}`);
    await this.wait(hold);
  }

  /** Pause on something worth reading. */
  async read(ms = 1600) {
    await this.wait(ms);
    await this.snap("read");
  }

  async target(locator) {
    const l = typeof locator === "string" ? this.page.locator(locator) : locator;
    await l.first().waitFor({ state: "visible", timeout: 20000 });
    await l.first().scrollIntoViewIfNeeded().catch(() => {});
    await this.wait(150);
    const box = await l.first().boundingBox();
    if (!box) throw new Error(`No box for ${l}`);
    return { l: l.first(), box };
  }

  async glide(x, y) {
    const [cx, cy] = this.cursor;
    const dist = Math.hypot(x - cx, y - cy);
    const ms = Math.round(Math.min(1000, Math.max(380, 300 + dist * 0.6)));
    this.cursor = [x, y];
    await this.page.evaluate(([x, y, ms]) => window.__pv?.move(x, y, ms), [x, y, ms]).catch(() => {});
    await this.wait(80);
  }

  async hover(locator) {
    const { box } = await this.target(locator);
    await this.glide(box.x + box.width / 2, box.y + box.height / 2);
    await this.page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await this.wait(500);
  }

  async click(locator, { dx = 0.5, dy = 0.5, after = 650 } = {}) {
    const { box } = await this.target(locator);
    const x = box.x + box.width * dx, y = box.y + Math.min(box.height * dy, box.height - 4);
    await this.glide(x, y);
    await this.page.mouse.move(x, y);
    await this.wait(120);
    await this.page.evaluate(() => window.__pv?.ring()).catch(() => {});
    this.log("click");
    await this.page.mouse.click(x, y);
    await this.wait(after);
    await this.snap("click");
  }

  /** Type into a field the way a quick person does. Masked fields stay masked. */
  async type(locator, text, { delay = 45, clear = true, after = 350 } = {}) {
    await this.click(locator, { after: 200 });
    const { l } = await this.target(locator);
    if (clear) await l.fill("");
    await this.page.keyboard.type(text, { delay });
    await this.wait(after);
    await this.snap("type");
  }

  async press(key, after = 500) {
    await this.page.keyboard.press(key);
    await this.wait(after);
  }

  async scroll(dy, { ms = 900 } = {}) {
    const steps = Math.max(6, Math.round(ms / 40));
    for (let i = 0; i < steps; i++) {
      await this.page.mouse.wheel(0, dy / steps);
      await this.wait(ms / steps);
    }
    await this.wait(300);
    await this.snap("scroll");
  }

  /** Zoom in gently on something small that matters, hold it, and zoom back out. */
  async zoom(locator, { hold = 2200, scale = 1.3 } = {}) {
    const { box } = await this.target(locator);
    this.log("zoom", { on: true, scale, rect: box });
    await this.wait(hold + 1200);
    this.log("zoom", { on: false });
    await this.wait(700);
    await this.snap("zoom");
  }

  /** Wait for slow work (a job, a provider) with the wait cut out of the video. */
  async cut(fn) {
    this.log("cut", { on: true });
    const out = await fn();
    await this.wait(300);
    this.log("cut", { on: false });
    return out;
  }

  /** Run something sped up in the video, such as a long scroll or a slow load. */
  async fast(fn, factor = 4) {
    this.log("fast", { on: true, factor });
    const out = await fn();
    this.log("fast", { on: false });
    return out;
  }

  /**
   * Show a text message arriving on the phone, as the phone itself would: the real message the
   * local SMS backend was given, word for word.
   */
  async sms(text, { hold = 4200 } = {}) {
    this.log("sms", { text });
    await this.page.evaluate(([text, hold]) => {
      const box = document.createElement("div");
      box.setAttribute("aria-hidden", "true");
      box.style.cssText = "position:fixed;left:10px;right:10px;top:-140px;z-index:2147483645;pointer-events:none;" +
        "background:rgba(250,250,248,.97);border-radius:22px;padding:14px 16px;box-shadow:0 12px 30px rgba(0,0,0,.25);" +
        "font:14px/1.35 system-ui,sans-serif;color:#141414;transition:top .45s cubic-bezier(.2,.9,.3,1.2)";
      const head = document.createElement("div");
      head.style.cssText = "display:flex;justify-content:space-between;color:#5e5e5a;font-size:12px;font-weight:600;margin-bottom:4px";
      head.append(Object.assign(document.createElement("span"), { textContent: "MESSAGES" }), Object.assign(document.createElement("span"), { textContent: "now" }));
      const from = Object.assign(document.createElement("div"), { textContent: "Finvest" });
      from.style.cssText = "font-weight:700;font-size:15px";
      box.append(head, from, Object.assign(document.createElement("div"), { textContent: text }));
      document.documentElement.appendChild(box);
      requestAnimationFrame(() => requestAnimationFrame(() => (box.style.top = "10px")));
      setTimeout(() => (box.style.top = "-160px"), hold);
      setTimeout(() => box.remove(), hold + 800);
    }, [text, hold]);
    await this.wait(1800);
  }

  async see(text, timeout = 20000) {
    await this.page.getByText(text).first().waitFor({ state: "visible", timeout });
  }
}
