// Personal details are blurred on screen while recording, so a video can be shared publicly.
// Everyone in the story is invented, but a phone number, an email, an ID number or a photo of an
// ID card still looks like personal data to a viewer or a video site, so none of it is shown.
//
// The same patterns serve two places: PRIVACY_SCRIPT, added to every page the recorder opens,
// draws a blurred patch over each match, and stack/front.mjs blurs the same matches on the PDF
// pages the test inbox shows.

/** What counts as a personal detail, as regular expression sources. */
export const PATTERNS = {
  email: String.raw`[\w.+-]+@[\w-]+(?:\.[\w-]+)+`,
  // Ugandan mobile numbers, with or without +256 or the leading 0, grouped or not.
  phone: String.raw`(?:\+?256[\s-]?|\b0)7\d{2}[\s-]?\d{3}[\s-]?\d{3}\b|\b7\d{2}[\s-]?\d{3}[\s-]?\d{3}\b`,
  // National ID numbers: CF or CM and twelve letters or digits.
  nin: String.raw`\bC[FM][0-9A-Z]{12}\b`,
  // Dates in a year someone alive could be born in, so a date of birth (the story's own dates
  // are all from 2026 on), in the forms the apps and documents print.
  birth: String.raw`\b\d{1,2}[\s/.-](?:\d{1,2}|[A-Z][a-z]{2,8})[\s/.-](?:19[2-9]\d|200\d)\b|\b(?:19[2-9]\d|200\d)-\d{2}-\d{2}\b`,
  // Finvest account numbers: ten digits, grouped 4, 3, 3, starting 10, 20, 30 or 40.
  account: String.raw`\b[1-4]0\d{2}[\s-]?\d{3}[\s-]?\d{3}\b`,
  // Details the apps already mask, such as CF91•••••••Q7K or •••• 418: blurred too.
  masked: String.raw`[0-9A-Z+]{0,6}•[• ]*•[ •]*[0-9A-Z]{0,4}`,
  // Bank account and card numbers: twelve or more digits in a row, or grouped in fours.
  bank: String.raw`\b\d{12,19}\b|\b\d{4}(?: \d{4}){2,3}(?: \d{1,5})?\b`,
};

/** One regular expression for all of them. */
export const PATTERN = Object.values(PATTERNS).map((p) => `(?:${p})`).join("|");

// Labels of fields whose whole value is personal, as soon as anything is typed.
const FIELD_LABELS = String.raw`phone|mobile|e-?mail|birth|\bNIN\b|national id|id number|account number|card number|card holder|expiry|cvv|security code|next of kin`;

export const PRIVACY_SCRIPT = `
(() => {
  if (window.__pvPrivacy) return;
  window.__pvPrivacy = true;
  const PATTERN = new RegExp(${JSON.stringify(PATTERN)}, "g");
  const LABELS = new RegExp(${JSON.stringify(FIELD_LABELS)}, "i");
  const PHOTO = /front|back|selfie|photo|id card|national id|document/i;
  const host = location.hostname;
  const isApp = /^(app|admin)\\./.test(host) || /pesapal|sandbox|iotec/i.test(host);
  let ranges = [];
  let dirty = true;
  let root = null;

  function rootEl() {
    if (root && root.isConnected) return root;
    root = document.createElement("div");
    root.id = "__pv_privacy";
    root.setAttribute("aria-hidden", "true");
    root.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483640;overflow:hidden";
    (document.body || document.documentElement).appendChild(root);
    return root;
  }

  function scanText() {
    const found = [];
    const body = document.body;
    if (!body) return found;
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    const runs = new Map();
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const p = n.parentNode;
      if (!p || p.id === "__pv_privacy" || p.closest?.("#__pv_privacy,script,style,noscript")) continue;
      let list = runs.get(p);
      if (!list) runs.set(p, (list = []));
      list.push(n);
    }
    for (const [, nodes] of runs) {
      let text = "";
      const at = [];
      for (const n of nodes) {
        at.push([text.length, n]);
        text += n.nodeValue;
      }
      PATTERN.lastIndex = 0;
      for (let m = PATTERN.exec(text); m; m = PATTERN.exec(text)) {
        if (!m[0]) { PATTERN.lastIndex++; continue; }
        const locate = (i, end) => {
          for (const [start, n] of at) {
            const len = n.nodeValue.length;
            if (end ? i > start && i <= start + len : i >= start && i < start + len) return [n, i - start];
          }
          const [start, n] = at[at.length - 1];
          return [n, Math.min(i - start, n.nodeValue.length)];
        };
        const [sn, so] = locate(m.index, false);
        const [en, eo] = locate(m.index + m[0].length, true);
        const r = document.createRange();
        try { r.setStart(sn, so); r.setEnd(en, eo); found.push(r); } catch {}
      }
    }
    return found;
  }

  function fieldIsPersonal(el) {
    if (el.type === "password" || el.type === "hidden") return false;
    const v = el.tagName === "SELECT" ? (el.selectedOptions?.[0]?.text ?? "") : el.value;
    // An example in an empty field, such as +256 772 123 456 or 22/06/1994, looks just as real.
    if (!v) { PATTERN.lastIndex = 0; return !!el.placeholder && PATTERN.test(el.placeholder); }
    PATTERN.lastIndex = 0;
    if (PATTERN.test(v)) return true;
    if (el.type === "tel" || el.type === "email") return true;
    const said = [el.getAttribute("aria-label"), el.placeholder, el.name, el.id, el.autocomplete,
      ...[...(el.labels ?? [])].map((l) => l.textContent),
      el.closest("fieldset,[role=group]")?.querySelector("legend,[role=heading],h2,h3")?.textContent,
      el.closest("fieldset,[role=group]")?.getAttribute("aria-label")].filter(Boolean).join(" ");
    return LABELS.test(said);
  }

  function isPhoto(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 60 || r.height < 60) return false;
    if (el.tagName === "VIDEO" || el.tagName === "CANVAS") return isApp;
    if (!isApp) return false;
    if (el.tagName === "IMG") {
      if (PHOTO.test(el.alt || "")) return true;
      return /id-check|identity/.test(location.pathname) && !/\\.svg/.test(el.currentSrc || "");
    }
    // React Native Web draws images as a background picture on a div.
    return /id-check/.test(location.pathname) && /url\\(/.test(getComputedStyle(el).backgroundImage);
  }

  function clip(el, rect) {
    let r = { l: rect.left, t: rect.top, r: rect.right, b: rect.bottom };
    for (let p = el; p && p !== document.documentElement; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (s.display === "none" || s.visibility === "hidden" || +s.opacity === 0) return null;
      if (s.overflow !== "visible" || s.overflowX !== "visible" || s.overflowY !== "visible") {
        const c = p.getBoundingClientRect();
        r = { l: Math.max(r.l, c.left), t: Math.max(r.t, c.top), r: Math.min(r.r, c.right), b: Math.min(r.b, c.bottom) };
      }
    }
    r = { l: Math.max(r.l, 0), t: Math.max(r.t, 0), r: Math.min(r.r, innerWidth), b: Math.min(r.b, innerHeight) };
    return r.r - r.l > 1 && r.b - r.t > 1 ? r : null;
  }

  function boxes() {
    const out = [];
    for (const range of ranges) {
      const el = range.startContainer.parentElement;
      if (!el || !el.isConnected) continue;
      for (const rect of range.getClientRects()) {
        const c = clip(el, rect);
        if (c) out.push(c);
      }
    }
    for (const el of document.querySelectorAll("input,textarea,select")) {
      if (!fieldIsPersonal(el)) continue;
      const c = clip(el, el.getBoundingClientRect());
      if (c) out.push(c);
    }
    for (const el of document.querySelectorAll(isApp ? "img,video,canvas,[style*=background-image],div" : "video,canvas")) {
      if (el.tagName === "DIV" && !/id-check/.test(location.pathname)) continue;
      if (el.tagName === "DIV" && !el.style.backgroundImage) continue;
      if (!isPhoto(el)) continue;
      const c = clip(el, el.getBoundingClientRect());
      if (c) out.push({ ...c, photo: true });
    }
    return out;
  }

  function draw() {
    try {
      paint();
    } catch (error) {
      window.__pvPrivacyError = String(error && error.stack || error);
    }
    requestAnimationFrame(draw);
  }

  function paint() {
    if (dirty) { ranges = scanText(); dirty = false; }
    const list = boxes();
    const host = rootEl();
    while (host.childElementCount > list.length) host.lastChild.remove();
    while (host.childElementCount < list.length) host.appendChild(document.createElement("div"));
    list.forEach((b, i) => {
      const pad = b.photo ? 0 : 3;
      const d = host.children[i];
      d.style.cssText = "position:absolute;border-radius:" + (b.photo ? "10px" : "5px") +
        ";left:" + (b.l - pad) + "px;top:" + (b.t - pad) + "px;width:" + (b.r - b.l + 2 * pad) +
        "px;height:" + (b.b - b.t + 2 * pad) + "px;backdrop-filter:blur(" + (b.photo ? 18 : 7) +
        "px);-webkit-backdrop-filter:blur(" + (b.photo ? 18 : 7) + "px);background:rgba(170,170,165," + (b.photo ? 0.55 : 0.45) + ")";
    });
  }

  const start = () => {
    // Mutation callbacks run before the browser paints, so a new screen is covered in the very
    // frame it first shows, and a scroll moves the patches in the frame it moves the page.
    new MutationObserver((records) => {
      if (!records.some((r) => !(r.target.id === "__pv_privacy" || r.target.parentElement?.id === "__pv_privacy"))) return;
      dirty = true;
      try { paint(); } catch (error) { window.__pvPrivacyError = String(error && error.stack || error); }
    }).observe(document, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["value", "src", "placeholder"] });
    addEventListener("scroll", () => { try { paint(); } catch {} }, { capture: true, passive: true });
    addEventListener("input", () => { try { paint(); } catch {} }, { capture: true });
    requestAnimationFrame(draw);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
`;
