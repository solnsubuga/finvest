/**
 * The web build keeps each screen's code in its own file, and Expo Router opens a screen whose
 * code is not loaded yet by drawing nothing: the client sees a blank page until the file has
 * arrived and React has shown it, which is at least a third of a second even from the cache.
 * scripts/route-chunks.mjs lists those files in index.html; once the first screen is up, this
 * loads them into the page one at a time, so that every later screen opens with its first frame
 * drawn and never shows a blank page in between.
 *
 * Loading a file only registers its code (Metro's `__d`); a screen's code still runs when the
 * screen first opens. The files go through the bundle loader Expo installs, so a screen opened
 * while its file is still on the way waits for the same request rather than asking twice. If the
 * loader is missing, the files are only fetched into the browser's cache, as before.
 *
 * Skipped when the client turned on data saving's light mode or the browser asks to save data:
 * screens then load when opened. The files are cached for a year and change only with a release.
 */
let done = false;

type SaveDataConnection = { saveData?: boolean };
type BundleLoader = (path: string) => Promise<void>;

/** Only this site's own screen files, whatever the page says. */
const SCREEN_FILE = /^\/_expo\/static\/js\/web\/[A-Za-z0-9_.()[\]-]+\.js$/;

function bundleLoader(): BundleLoader | null {
  const runtime = globalThis as typeof globalThis & Record<string, unknown>;
  const prefix = typeof runtime.__METRO_GLOBAL_PREFIX__ === "string" ? runtime.__METRO_GLOBAL_PREFIX__ : "";
  const loader = runtime[`${prefix}__loadBundleAsync`];
  return typeof loader === "function" ? (loader as BundleLoader) : null;
}

/** Lets the browser draw and answer taps between two files. */
function nextIdle(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestIdleCallback === "function") {
      requestIdleCallback(() => resolve(), { timeout: 200 });
    } else {
      setTimeout(resolve, 0);
    }
  });
}

async function loadAll(load: BundleLoader, paths: string[]): Promise<void> {
  for (const path of paths) {
    await nextIdle();
    try {
      await load(path);
    } catch {
      // Offline, or the file is gone after a release: the screen loads when it opens, and says
      // so if it cannot (ScreenLoadFailed).
    }
  }
}

function prefetchOnly(paths: string[]): void {
  for (const path of paths) {
    const link = document.createElement("link");
    link.rel = "prefetch";
    link.as = "script";
    link.href = path;
    document.head.appendChild(link);
  }
}

/** Starts loading the other screens' code. Resolves when every file has been tried. */
export function prefetchRoutes(): Promise<void> {
  if (done || typeof document === "undefined") {
    return Promise.resolve();
  }
  done = true;
  const connection = (navigator as Navigator & { connection?: SaveDataConnection }).connection;
  if (connection?.saveData === true) {
    return Promise.resolve();
  }
  const listed = document.querySelector('meta[name="finvest-route-chunks"]')?.getAttribute("content") ?? "";
  const paths = listed.split(" ").filter((path) => SCREEN_FILE.test(path));
  const load = bundleLoader();
  if (load === null) {
    prefetchOnly(paths);
    return Promise.resolve();
  }
  return loadAll(load, paths);
}

/** For tests: lets prefetchRoutes run again. */
export function resetPrefetchForTests(): void {
  done = false;
}
