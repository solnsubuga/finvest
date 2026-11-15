import { useEffect, useState } from "react";
import { StyleSheet, View } from "react-native";
import { initialWindowMetrics, SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";
import type { ErrorBoundaryProps } from "expo-router";

import { Button } from "@/components/Button";
import { LogoMark } from "@/components/Logo";
import { Txt } from "@/components/Txt";
import { hideStartLoader } from "@/navigation/startLoader";
import { colors } from "@/theme";

import { canReloadPage, isOffline, onBackOnline, reloadPage } from "./reloadPage";

/**
 * A screen whose code could not be fetched (web, offline on first open) or that failed to draw.
 * The web build loads each screen's code when it first opens, and a failed load would otherwise
 * leave a blank page. Exported as the root layout's ErrorBoundary, so it is drawn outside the
 * app's providers and uses nothing that needs them.
 */
export function ScreenLoadFailed({ error, retry }: ErrorBoundaryProps) {
  return (
    <SafeAreaProvider initialMetrics={initialWindowMetrics ?? NO_INSETS}>
      <LoadFailedPage error={error} retry={retry} />
    </SafeAreaProvider>
  );
}

// Drawn at once, before the provider has measured the window: the page pads its top anyway.
const NO_INSETS = {
  frame: { x: 0, y: 0, width: 0, height: 0 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 },
};

/** Metro names a failed screen fetch AsyncRequireError; browsers say "Loading chunk" or similar. */
export function isLoadError(error: Error): boolean {
  return (
    error.name === "AsyncRequireError" ||
    error.name === "ChunkLoadError" ||
    /loading (css )?chunk|failed to fetch|load failed|importing a module script failed/i.test(error.message)
  );
}

function LoadFailedPage({ error, retry }: ErrorBoundaryProps) {
  const insets = useSafeAreaInsets();
  const [offline, setOffline] = useState(isOffline);
  const [busy, setBusy] = useState(false);
  const loadError = isLoadError(error) || offline;

  useEffect(() => {
    // The start loader covers the page until a screen is drawn. This is the screen now.
    hideStartLoader();
  }, []);

  useEffect(() => {
    if (!offline) return undefined;
    // Back online: load the screen without waiting for a tap.
    return onBackOnline(() => {
      setOffline(false);
      if (loadError && canReloadPage()) reloadPage();
    });
  }, [offline, loadError]);

  const tryAgain = () => {
    setOffline(isOffline());
    if (isOffline()) return;
    if (loadError && canReloadPage()) {
      setBusy(true);
      reloadPage();
      return;
    }
    void retry();
  };

  const title = offline ? "No connection" : loadError ? "This screen did not load" : "Something went wrong";
  const detail = offline
    ? "This screen needs the internet to open the first time. Check your data or Wi-Fi. It opens by itself when you are back online."
    : loadError
      ? "Part of the app did not arrive. Your money and anything you saved are not affected. Try again to load it."
      : "This screen hit a problem. Your money and anything you saved are not affected. Try again, or ask us in Help if it keeps happening.";

  return (
    <View
      style={[styles.page, { paddingTop: insets.top + 56, paddingBottom: insets.bottom + 28 }]}
      testID="screen-load-failed"
    >
      <View style={styles.column}>
        <LogoMark size={48} />
        <View style={{ gap: 10 }} accessibilityRole="alert" accessibilityLiveRegion="polite">
          <Txt accessibilityRole="header" size={28} weight="extrabold" lineHeight={34} style={{ letterSpacing: -0.5 }}>
            {title}
          </Txt>
          <Txt size={15} lineHeight={22} color={colors.muted}>
            {detail}
          </Txt>
        </View>
        <View style={{ flex: 1 }} />
        <Button label="Try again" onPress={tryAgain} loading={busy} testID="screen-load-retry" />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.paper, paddingHorizontal: 24, alignItems: "center" },
  column: { flex: 1, width: "100%", maxWidth: 440, gap: 20 },
});
