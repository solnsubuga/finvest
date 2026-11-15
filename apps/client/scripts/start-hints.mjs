// Adds hints to dist/index.html that let the web app start sooner. Run after `expo export
// --platform web`.
//
// Without them the browser finds out what the first screen needs one step at a time: it fetches
// the scripts, runs them, and only then asks for the font files, for the client's session from
// the API, and for the first screen's own code, one file after another. These tags let it fetch
// all of that alongside the scripts (rel="preload") and open the connection to the API while the
// scripts are still arriving (rel="preconnect").
//
// The start-up scripts sit at the end of the page, where the browser gives them its lowest
// priority, so they are named here first at high priority, and everything else is fetched at
// low priority behind them: the scripts are the longest step, and they run while the rest
// arrives.
//
// The fonts are the ones src/theme/index.ts loads at start-up (fontAssets). The build fails if
// one is missing, so a change there is not silently left without its hint.
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const app = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(app, "dist");
const page = join(dist, "index.html");
const html = readFileSync(page, "utf8");
if (html.includes('data-finvest-hint')) {
  console.error("dist/index.html already has its start hints");
  process.exit(1);
}

const theme = readFileSync(join(app, "src", "theme", "index.ts"), "utf8");
const block = theme.match(/export const fontAssets = \{([^}]*)\}/);
const fonts = block ? [...block[1].matchAll(/\b(PlusJakartaSans_\w+)\b/g)].map((match) => match[1]) : [];
if (fonts.length === 0) {
  console.error("Found no fonts in fontAssets in src/theme/index.ts");
  process.exit(1);
}

function files(folder) {
  return readdirSync(folder).flatMap((name) => {
    const path = join(folder, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}
const assets = files(join(dist, "assets")).map((path) => "/" + relative(dist, path).split(sep).join("/"));
const bundle = readdirSync(join(dist, "_expo", "static", "js", "web"))
  .map((name) => readFileSync(join(dist, "_expo", "static", "js", "web", name), "utf8"))
  .join("\n");

const tags = fonts.map((font) => {
  const found = assets.filter((path) => new RegExp(`/${font}\\.[0-9a-f]+\\.ttf$`).test(path));
  if (found.length !== 1) {
    console.error(`Expected one font file for ${font}, found ${found.length}`);
    process.exit(1);
  }
  // The app asks for the file by exactly this address, or the preload would be fetched twice.
  if (!bundle.includes(`"${found[0]}"`)) {
    console.error(`The scripts do not load ${found[0]}`);
    process.exit(1);
  }
  if (!/^[A-Za-z0-9_.@+/-]+$/.test(found[0])) {
    console.error(`Unexpected font address: ${found[0]}`);
    process.exit(1);
  }
  return `<link data-finvest-hint rel="preload" as="font" type="font/ttf" crossorigin fetchpriority="low" href="${found[0]}"/>`;
});

// The first screen's own code. Expo Router keeps each route in its own file and asks for it only
// once the scripts have run and found the route, then for the next one inside it, so each costs a
// round trip in turn. These are the routes a first visit to the app opens: the root layout, the
// signed-in tabs and Home (where "/" goes), and the signed-out frame and Welcome (where Home sends
// someone not signed in). The entry script maps each route file to its chunk:
//   "./(auth)/welcome.tsx":{enumerable:!0,get:()=>r(d[1])(d[26],d.paths)}  ...  },9,{"26":786,...,"paths":{"786":"/_expo/.../welcome-<hash>.js"}}
const FIRST_ROUTES = ["./_layout.tsx", "./(app)/_layout.tsx", "./(app)/index.tsx", "./(auth)/_layout.tsx", "./(auth)/welcome.tsx"];
const scripts = join(dist, "_expo", "static", "js", "web");
const entryName = readdirSync(scripts).find((name) => /^entry-[0-9a-f]+\.js$/.test(name));
const entry = entryName ? readFileSync(join(scripts, entryName), "utf8") : "";
const firstChunks = FIRST_ROUTES.map((route) => {
  const quoted = JSON.stringify(route).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const key = new RegExp(`${quoted}:\\{enumerable:!0,get:\\(\\)=>\\w+\\(\\w+\\[\\d+\\]\\)\\(\\w+\\[(\\d+)\\],\\w+\\.paths\\)\\}`).exec(entry);
  if (!key) {
    console.error(`The entry script does not load ${route} as its own file`);
    process.exit(1);
  }
  const deps = /\},\d+,(\{"0":[^\n]*\}\})\);/g;
  deps.lastIndex = key.index;
  const found = deps.exec(entry);
  const table = found ? JSON.parse(found[1]) : null;
  const path = table?.paths?.[table[key[1]]];
  if (typeof path !== "string" || !/^\/_expo\/static\/js\/web\/[A-Za-z0-9_.()[\]-]+\.js$/.test(path)) {
    console.error(`Found no file for ${route}`);
    process.exit(1);
  }
  return path;
});
// No crossorigin: the app adds these as plain script tags, and the preload must match to be used.
for (const path of new Set(firstChunks)) {
  tags.push(`<link data-finvest-hint rel="preload" as="script" fetchpriority="low" href="${path}"/>`);
}

// The address the build talks to (src/api/config.ts): production when nothing else is set.
const api = (process.env.EXPO_PUBLIC_API_URL || "https://api.finvestug.com").replace(/\/+$/, "");
const origin = new URL(api).origin;
if (!/^https?:\/\/[a-z0-9.-]+(:[0-9]+)?$/.test(origin)) {
  console.error(`Unexpected API address: ${api}`);
  process.exit(1);
}
// The start-up scripts, exactly as the page loads them (plain script tags, so no crossorigin).
const startScripts = [...html.matchAll(/<script src="([^"]+)" defer><\/script>/g)].map((match) => match[1]);
if (startScripts.length === 0 || !startScripts.every((path) => /^\/_expo\/static\/js\/web\/[A-Za-z0-9_.-]+\.js$/.test(path))) {
  console.error("Found no start-up scripts in dist/index.html");
  process.exit(1);
}
tags.unshift(...startScripts.map((path) => `<link data-finvest-hint rel="preload" as="script" fetchpriority="high" href="${path}"/>`));
// The session cookie goes with each call, so the connection to open is the credentialed one.
tags.unshift(`<link data-finvest-hint rel="preconnect" href="${origin}" crossorigin="use-credentials"/>`);

if (!/<\/head>/.test(html)) {
  console.error("dist/index.html has no </head>");
  process.exit(1);
}
writeFileSync(page, html.replace("</head>", `${tags.join("")}</head>`));
console.log(`Added ${tags.length} start hints to dist/index.html`);
