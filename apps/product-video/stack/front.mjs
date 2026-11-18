// The local front door for the product video. One HTTPS server on 127.0.0.1:443 that plays
// every public host, so the browser's address bar shows the real addresses while nothing
// leaves this machine:
//
//   finvestug.com, www.     the website build (apps/site/dist)
//   app.finvestug.com       the client app's web build (apps/client/dist)
//   admin.finvestug.com     the console build (apps/console/dist)
//   api.finvestug.com       proxied to the local API on 127.0.0.1:8000
//   storage.local.test      a tiny S3 stand-in that keeps files on disk
//   resend.local.test       a Resend stand-in that keeps every email on disk
//   verify.local.test       an ioTec Verify stand-in for phone name lookups (no sandbox exists)
//   mail.local.test         the test inbox that shows those emails
//
// The browser reaches these hosts only through --host-resolver-rules (see lib/browser.mjs),
// and the backend reaches the .local.test hosts through /etc/hosts. Nothing here is used in
// any deployment.
//
//   node front.mjs <work dir> <repo root>

import { createServer } from "node:https";
import { PATTERN } from "../lib/privacy.mjs";
import { request as httpRequest } from "node:http";
import { createHash, randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import os from "node:os";
import fs from "node:fs";
import path from "node:path";

const [work, repo] = process.argv.slice(2);
if (!work || !repo) throw new Error("usage: node front.mjs <work dir> <repo root>");

const certs = path.join(work, "certs");
const storageDir = path.join(work, "storage");
const mailDir = path.join(work, "mail");
fs.mkdirSync(storageDir, { recursive: true });
fs.mkdirSync(mailDir, { recursive: true });

const sites = {
  "finvestug.com": path.join(repo, "apps/site/dist"),
  "www.finvestug.com": path.join(repo, "apps/site/dist"),
  "app.finvestug.com": path.join(repo, "apps/client/dist"),
  "admin.finvestug.com": path.join(repo, "apps/console/dist"),
};

const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".wasm": "application/wasm",
  ".pdf": "application/pdf",
  ".txt": "text/plain; charset=utf-8",
  ".xml": "application/xml",
  ".webmanifest": "application/manifest+json",
  ".onnx": "application/octet-stream",
};

function sendFile(res, file, status = 200) {
  res.writeHead(status, {
    "content-type": types[path.extname(file)] ?? "application/octet-stream",
    "cache-control": "no-cache",
  });
  fs.createReadStream(file).pipe(res);
}

function isFile(file) {
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

// Caddy's rules in infra/deployment: the file itself, then for the website `path/index.html`
// and the 404 page, and for the two apps the app's own index.html.
function serveStatic(host, root, req, res) {
  const url = new URL(req.url, `https://${host}`);
  const clean = path.normalize(decodeURIComponent(url.pathname)).replace(/^(\.\.[/\\])+/, "");
  const file = path.join(root, clean);
  if (!file.startsWith(root)) return res.writeHead(400).end();
  if (isFile(file)) return sendFile(res, file);
  const isSite = host === "finvestug.com" || host === "www.finvestug.com";
  if (isSite) {
    if (isFile(path.join(file, "index.html"))) return sendFile(res, path.join(file, "index.html"));
    if (isFile(file + ".html")) return sendFile(res, file + ".html");
    return sendFile(res, path.join(root, "404.html"), 404);
  }
  return sendFile(res, path.join(root, "index.html"));
}

function proxyApi(req, res) {
  const upstream = httpRequest(
    {
      host: "127.0.0.1",
      port: 8000,
      method: req.method,
      path: req.url,
      headers: { ...req.headers, host: "api.finvestug.com" },
    },
    (answer) => {
      res.writeHead(answer.statusCode ?? 502, answer.headers);
      answer.pipe(res);
    },
  );
  upstream.on("error", () => {
    if (!res.headersSent) res.writeHead(502, { "content-type": "text/plain" });
    res.end("The local API is not running.");
  });
  req.pipe(upstream);
}

function body(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

// S3 stand-in: path style, PUT, GET, HEAD and DELETE on /<bucket>/<key>. Signatures are not
// checked, because only this machine can reach it.
async function storage(req, res) {
  const url = new URL(req.url, "https://storage.local.test");
  const key = decodeURIComponent(url.pathname).replace(/^\/+/, "");
  if (!key || key.includes("..")) return res.writeHead(400).end();
  const file = path.join(storageDir, createHash("sha256").update(key).digest("hex"));
  if (req.method === "PUT") {
    const data = await body(req);
    fs.writeFileSync(file, data);
    fs.writeFileSync(
      file + ".json",
      JSON.stringify({ key, type: req.headers["content-type"] ?? "application/octet-stream" }),
    );
    const etag = createHash("md5").update(data).digest("hex");
    return res.writeHead(200, { etag: `"${etag}"` }).end();
  }
  if (!isFile(file)) {
    res.writeHead(404, { "content-type": "application/xml" });
    return res.end("<Error><Code>NoSuchKey</Code></Error>");
  }
  if (req.method === "DELETE") {
    fs.rmSync(file, { force: true });
    fs.rmSync(file + ".json", { force: true });
    return res.writeHead(204).end();
  }
  const meta = JSON.parse(fs.readFileSync(file + ".json", "utf8"));
  const size = fs.statSync(file).size;
  res.writeHead(200, {
    "content-type": meta.type,
    "content-length": size,
    "access-control-allow-origin": "*",
  });
  if (req.method === "HEAD") return res.end();
  fs.createReadStream(file).pipe(res);
}

// Resend stand-in: POST /emails keeps the message and answers as Resend does.
async function resend(req, res) {
  if (req.method !== "POST" || !req.url.startsWith("/emails")) return res.writeHead(404).end();
  const message = JSON.parse((await body(req)).toString("utf8"));
  const id = randomUUID();
  const stamp = new Date().toISOString();
  fs.writeFileSync(
    path.join(mailDir, `${Date.now()}-${id}.json`),
    JSON.stringify({ id, received: stamp, ...message }),
  );
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify({ id }));
}

// ioTec Verify stand-in for the one request the app sends it here, the name a network holds for
// a payout number. ioTec offers no sandbox, so locally the invented people's own numbers answer
// with their own names, as a network would. Any other number is not found.
const NETWORK_NAMES = { "0700000418": "ESTHER NABUKENYA", "0700000736": "MOSES OKELLO" };
const lookups = new Map();
async function verify(req, res) {
  const json = (status, value) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(value));
  };
  if (req.method === "POST" && req.url === "/api/verify-phone-number") {
    const ask = JSON.parse((await body(req)).toString("utf8"));
    const id = randomUUID();
    lookups.set(id, { phone: ask.phoneNumber, at: Date.now() });
    return json(200, { id, status: "Pending" });
  }
  const read = req.url.match(/^\/api\/verify-phone-number\/([0-9a-f-]{36})$/);
  if (req.method === "GET" && read && lookups.has(read[1])) {
    const { phone, at } = lookups.get(read[1]);
    if (Date.now() - at < 3000) return json(200, { id: read[1], status: "Processing" });
    const name = NETWORK_NAMES[phone];
    return json(200, name
      ? { id: read[1], status: "Completed", verificationStatus: "Success", registeredName: name }
      : { id: read[1], status: "Completed", verificationStatus: "NotFound" });
  }
  return json(404, { message: "Not found" });
}

function messages() {
  return fs
    .readdirSync(mailDir)
    .filter((f) => f.endsWith(".json"))
    .sort()
    .reverse()
    .map((f) => JSON.parse(fs.readFileSync(path.join(mailDir, f), "utf8")));
}

const escape = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const inboxStyle = `
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.45 "Plus Jakarta Sans", system-ui, sans-serif; color: #141414; background: #f4f4f1; }
  header { display: flex; align-items: center; gap: 12px; padding: 14px 24px; background: #0d0d0d; color: #fff; }
  header b { font-size: 17px; }
  header span { color: #b6eb0a; font-size: 13px; }
  main { display: grid; grid-template-columns: 380px 1fr; height: calc(100vh - 52px); }
  nav { overflow: auto; border-right: 1px solid #e2e2dc; background: #fff; }
  nav a { display: block; padding: 14px 20px; border-bottom: 1px solid #eee; color: inherit; text-decoration: none; }
  nav a.on { background: #f1fbd6; }
  nav a small { display: block; color: #5e5e5a; }
  section { overflow: auto; padding: 24px 32px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .meta { color: #5e5e5a; margin-bottom: 16px; }
  iframe { width: 100%; height: 70vh; border: 1px solid #e2e2dc; border-radius: 12px; background: #fff; }
  .att { display: inline-flex; gap: 8px; align-items: center; margin: 0 8px 12px 0; padding: 8px 14px; border-radius: 999px; background: #fff; border: 1px solid #d7d7cf; color: inherit; text-decoration: none; }
  .empty { color: #5e5e5a; padding: 40px; }
`;

// One-time links carry their token after "#". The buttons still work, but a link printed as
// text is hidden, so a recording never shows a token.
const hideLinks = (html) =>
  html.replace(/(>\s*)https:\/\/[^\s<]*#[^\s<]*(\s*<)/g, "$1(one-time link, hidden in the test inbox)$2");

function inbox(req, res) {
  const url = new URL(req.url, "https://mail.local.test");
  const all = messages();
  const att = url.pathname.match(/^\/attachment\/([^/]+)\/(\d+)$/);
  if (att) {
    const m = all.find((x) => x.id === att[1]);
    const a = m?.attachments?.[Number(att[2])];
    if (!a) return res.writeHead(404).end();
    const data = Buffer.from(a.content, "base64");
    res.writeHead(200, {
      "content-type": a.content_type ?? (a.filename?.endsWith(".pdf") ? "application/pdf" : "application/octet-stream"),
      "content-disposition": `inline; filename="${escape(a.filename)}"`,
    });
    return res.end(data);
  }
  // A PDF reader for attachments, as a mail app has. A locked PDF asks for its password, and
  // poppler opens the very file the email carried with what was typed: a wrong password fails.
  const viewer = url.pathname.match(/^\/view\/([^/]+)\/(\d+)$/);
  if (viewer) return viewPdf(req, res, all, viewer[1], Number(viewer[2]));
  if (url.pathname === "/messages.json") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify(all.map(({ attachments, html, ...m }) => ({ ...m, attachments: (attachments ?? []).map((a) => a.filename) }))));
  }
  const id = url.searchParams.get("m") ?? all[0]?.id;
  const open = all.find((m) => m.id === id);
  const to = (m) => (Array.isArray(m.to) ? m.to.join(", ") : m.to);
  const list = all
    .map(
      (m) =>
        `<a href="/?m=${m.id}" class="${m.id === id ? "on" : ""}" data-subject="${escape(m.subject)}"><b>${escape(m.subject)}</b><small>${escape(to(m))}</small></a>`,
    )
    .join("");
  const view = open
    ? `<h1>${escape(open.subject)}</h1>
       <div class="meta">From ${escape(open.from)} to ${escape(to(open))}</div>
       ${(open.attachments ?? [])
         .map((a, i) => `<a class="att" data-attachment="${escape(a.filename)}" href="/${a.filename?.endsWith(".pdf") ? "view" : "attachment"}/${open.id}/${i}">📎 ${escape(a.filename)}</a>`)
         .join("")}
       <iframe title="Message" srcdoc="${escape(`<base target="_top">` + hideLinks(open.html ?? `<pre>${escape(open.text)}</pre>`))}"></iframe>`
    : `<div class="empty">No messages yet.</div>`;
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  res.end(`<!doctype html><html><head><meta charset="utf-8"><title>Test inbox</title><style>${inboxStyle}</style></head>
    <body><header><b>Test inbox</b><span>Emails from the demo land here. Nothing is delivered.</span></header>
    <main><nav>${list}</nav><section>${view}</section></main></body></html>`);
}

async function viewPdf(req, res, all, id, index) {
  const m = all.find((x) => x.id === id);
  const a = m?.attachments?.[index];
  if (!a) return res.writeHead(404).end();
  let body = "";
  for await (const chunk of req) body += chunk;
  const password = req.method === "POST" ? (new URLSearchParams(body).get("password") ?? "") : null;
  let pages = null;
  let patches = [];
  let problem = "";
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "pv-pdf-"));
  try {
    const file = path.join(tmp, "a.pdf");
    fs.writeFileSync(file, Buffer.from(a.content, "base64"));
    const locked = (() => {
      try {
        execFileSync("pdfinfo", [file], { stdio: "pipe" });
        return false;
      } catch {
        return true;
      }
    })();
    if (!locked || password !== null) {
      try {
        execFileSync("pdftoppm", [...(locked ? ["-upw", password] : []), "-r", "110", "-png", file, path.join(tmp, "p")], { stdio: "pipe" });
        pages = fs.readdirSync(tmp).filter((f) => f.endsWith(".png")).sort().map((f) => fs.readFileSync(path.join(tmp, f)).toString("base64"));
        const layout = execFileSync("pdftotext", [...(locked ? ["-upw", password] : []), "-bbox-layout", file, "-"], { encoding: "utf8" });
        patches = personalPatches(layout);
      } catch {
        problem = "That password did not open it.";
      }
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  const content = pages
    ? pages
        .map((b64, i) => `<div class="sheet"><img class="page" alt="Page" src="data:image/png;base64,${b64}">${(patches[i] ?? [])
          .map(([l, t, w, h]) => `<i class="blur" style="left:${l}%;top:${t}%;width:${w}%;height:${h}%"></i>`)
          .join("")}</div>`)
        .join("")
    : `<form method="post" class="lock"><h2>This PDF is locked</h2><p>Enter its password to open it.</p>
         <input name="password" type="password" aria-label="Password" autofocus>
         <button type="submit">Open</button><p class="bad">${escape(problem)}</p></form>`;
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  res.end(`<!doctype html><html><head><meta charset="utf-8"><title>${escape(a.filename)}</title><style>${inboxStyle}
    body { display: block; } .doc { padding: 24px; display: flex; flex-direction: column; align-items: center; gap: 16px; overflow: auto; height: calc(100vh - 60px); }
    .sheet { position: relative; width: min(900px, 100%); line-height: 0; }
    .page { width: 100%; box-shadow: 0 2px 16px rgba(0,0,0,.12); background: #fff; }
    .blur { position: absolute; border-radius: 4px; backdrop-filter: blur(7px); background: rgba(170,170,165,.45); }
    .lock { background: #fff; border: 1px solid #e2e2dc; border-radius: 16px; padding: 28px 32px; width: 380px; margin-top: 80px; }
    .lock input { width: 100%; box-sizing: border-box; font-size: 18px; padding: 12px 14px; border-radius: 10px; border: 1px solid #c9c9c0; margin: 8px 0 14px; }
    .lock button { font-size: 16px; font-weight: 700; padding: 12px 22px; border-radius: 999px; border: 0; background: #0e0e0e; color: #fff; }
    .bad { color: #b42318; min-height: 1em; }</style></head>
    <body><header><b>Test inbox</b><span>${escape(a.filename)}</span></header><div class="doc">${content}</div></body></html>`);
}

const server = createServer(
  {
    key: fs.readFileSync(path.join(certs, "server.key")),
    cert: fs.readFileSync(path.join(certs, "server.crt")),
  },
  async (req, res) => {
    const host = (req.headers.host ?? "").split(":")[0];
    try {
      if (host === "api.finvestug.com") return proxyApi(req, res);
      if (host === "storage.local.test") return await storage(req, res);
      if (host === "resend.local.test") return await resend(req, res);
      if (host === "verify.local.test") return await verify(req, res);
      if (host === "mail.local.test") return inbox(req, res);
      const root = sites[host];
      if (root) return serveStatic(host, root, req, res);
      res.writeHead(421).end("Unknown host");
    } catch (error) {
      console.error(error);
      if (!res.headersSent) res.writeHead(500);
      res.end();
    }
  },
);
server.listen(443, "127.0.0.1", () => console.log("front door on https://127.0.0.1:443"));

// Where the personal details sit on each PDF page, as percentages of the page, so the inbox can
// blur them the way the recorder blurs them on screen (lib/privacy.mjs). Reads the word boxes
// of \`pdftotext -bbox-layout\`, line by line.
function personalPatches(xml) {
  const unescape = (t) => t.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");
  return [...xml.matchAll(/<page width="([\d.]+)" height="([\d.]+)">([\s\S]*?)<\/page>/g)].map(([, pw, ph, body]) => {
    const out = [];
    for (const [, line] of body.matchAll(/<line[^>]*>([\s\S]*?)<\/line>/g)) {
      const words = [...line.matchAll(/<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<\/word>/g)].map(
        ([, x0, y0, x1, y1, w]) => ({ box: [+x0, +y0, +x1, +y1], text: unescape(w) }),
      );
      let text = "";
      const at = words.map((w) => {
        const start = text.length ? text.length + 1 : 0;
        text += (text.length ? " " : "") + w.text;
        return [start, start + w.text.length];
      });
      for (const m of text.matchAll(new RegExp(PATTERN, "g"))) {
        const hit = words.filter((_, k) => at[k][1] > m.index && at[k][0] < m.index + m[0].length);
        if (!hit.length) continue;
        const x0 = Math.min(...hit.map((w) => w.box[0])) - 2;
        const y0 = Math.min(...hit.map((w) => w.box[1])) - 2;
        const x1 = Math.max(...hit.map((w) => w.box[2])) + 2;
        const y1 = Math.max(...hit.map((w) => w.box[3])) + 2;
        out.push([(100 * x0) / pw, (100 * y0) / ph, (100 * (x1 - x0)) / pw, (100 * (y1 - y0)) / ph]);
      }
    }
    return out;
  });
}
