import { useEffect } from "react";
import { useFonts } from "expo-font";

import { hideStartLoader } from "@/navigation/startLoader";
import { fontAssets } from "@/theme";

/**
 * Called by the frames every first screen is drawn in (Screen, and the signed-in tab bar and
 * sidebar): once one of them is on the page with the app's font, the web page's start loader
 * goes. Later calls do nothing. A screen can come up before the font when the session is looked
 * up first (the unlock and frozen account screens), so this waits for the font too, which the
 * root layout is already loading. The root layout also removes the loader after
 * START_LOADER_LIMIT_MS, so a screen drawn without these frames never stays hidden behind it.
 */
export function useStartLoaderDone(): void {
  const [loaded, error] = useFonts(fontAssets);
  const fontsReady = loaded || Boolean(error);

  useEffect(() => {
    if (!fontsReady) {
      return undefined;
    }
    // After the browser has painted the screen, so the loader never fades out onto a blank page.
    const frame = requestAnimationFrame(() => hideStartLoader());
    return () => cancelAnimationFrame(frame);
  }, [fontsReady]);
}

/** How long the root layout waits, once fonts are in, before removing the loader regardless. */
export const START_LOADER_LIMIT_MS = 8000;
