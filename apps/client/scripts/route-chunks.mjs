// Lists the web build's route chunks in dist/index.html, so the app can fetch them ahead of time.
// Run after `expo export --platform web`.
//
// Expo Router splits the web build by route: each screen's code is a separate file under
// /_expo/static/js/web/, fetched the first time the screen opens. Until it arrives the screen is
// blank, which on a slow connection lasts seconds. The page loads only the entry files itself;
// this writes the others into a meta tag that src/navigation/prefetchRoutes.web.ts reads to fetch
// them while the client is on the first screen (unless they asked to save data). The files carry
// a hash in their names and are cached for a year, so each release fetches them once.
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dist = join(dirname(fileURLToPath(import.meta.url)), "..", "dist");
const folder = "/_expo/static/js/web/";
const page = join(dist, "index.html");
const html = readFileSync(page, "utf8");
if (html.includes('name="finvest-route-chunks"')) {
  console.error("dist/index.html already lists its route chunks");
  process.exit(1);
}

const chunks = readdirSync(join(dist, folder))
  .filter((name) => name.endsWith(".js"))
  .map((name) => {
    // The loader asks for the name exactly as written ([kind]-<hash>.js), so it is listed as is.
    if (!/^[A-Za-z0-9_.()[\]-]+$/.test(name)) {
      console.error(`Unexpected chunk name: ${name}`);
      process.exit(1);
    }
    return folder + name;
  })
  .filter((path) => !html.includes(`src="${path}"`))
  .sort();
if (!/<\/head>/.test(html)) {
  console.error("dist/index.html has no </head>");
  process.exit(1);
}
const tag = `<meta name="finvest-route-chunks" content="${chunks.join(" ")}"/>`;
writeFileSync(page, html.replace("</head>", `${tag}</head>`));
console.log(`Listed ${chunks.length} route chunks in dist/index.html`);
