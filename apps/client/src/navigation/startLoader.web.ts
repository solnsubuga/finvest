/**
 * The web page paints a loader from index.html alone (apps/client/public/index.html), before any
 * script has arrived, so the browser never shows an empty page while the app starts. This fades
 * it out and removes it once the first screen is up. The loader sits outside React's root, so
 * React never removes it on its own.
 */
export const START_LOADER_ID = "finvest-start";

/** Matches the fade in index.html, with a little to spare. */
export const START_LOADER_FADE_MS = 220;

export function hideStartLoader(): void {
  if (typeof document === "undefined") {
    return;
  }
  const loader = document.getElementById(START_LOADER_ID);
  if (!loader || loader.hasAttribute("data-leaving")) {
    return;
  }
  loader.setAttribute("data-leaving", "");
  loader.setAttribute("aria-busy", "false");
  setTimeout(() => loader.remove(), START_LOADER_FADE_MS);
}
