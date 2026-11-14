/**
 * The web build fetches each screen's code the first time the screen opens, and shows a blank
 * page until it arrives, which on a slow connection lasts seconds. scripts/route-chunks.mjs lists
 * those files in index.html; this asks the browser to fetch them at its lowest priority while the
 * client is on the first screen, so later screens open from the browser's cache. The files are
 * cached for a year and change only with a release.
 *
 * Skipped when the client turned on data saving's light mode or the browser asks to save data:
 * screens then load when opened, as before.
 */
let done = false;

type SaveDataConnection = { saveData?: boolean };

export function prefetchRoutes(): void {
  if (done || typeof document === "undefined") {
    return;
  }
  done = true;
  const connection = (navigator as Navigator & { connection?: SaveDataConnection }).connection;
  if (connection?.saveData === true) {
    return;
  }
  const listed = document.querySelector('meta[name="finvest-route-chunks"]')?.getAttribute("content") ?? "";
  for (const path of listed.split(" ")) {
    // Only this site's own screen files, whatever the page says.
    if (!/^\/_expo\/static\/js\/web\/[A-Za-z0-9_.()[\]-]+\.js$/.test(path)) {
      continue;
    }
    const link = document.createElement("link");
    link.rel = "prefetch";
    link.as = "script";
    link.href = path;
    document.head.appendChild(link);
  }
}

/** For tests: lets prefetchRoutes run again. */
export function resetPrefetchForTests(): void {
  done = false;
}
