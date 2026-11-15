import { StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Txt } from "@/components/Txt";
import { colors } from "@/theme";

/**
 * A strip at the top of every screen while the app cannot reach Finvest. What is on the
 * screen stays, and the app keeps trying by itself every few seconds and as soon as the device
 * says it is back online. It has no button of its own: a screen that could not load already
 * offers "Try again", and two buttons saying the same thing read as two different things.
 */
export function OfflineBanner() {
  const insets = useSafeAreaInsets();
  return (
    <View
      style={[styles.wrap, { top: insets.top + 8 }]}
      pointerEvents="box-none"
      accessibilityRole="alert"
      accessibilityLiveRegion="polite"
      testID="offline-banner"
    >
      <View style={styles.banner}>
        <View style={styles.dot} />
        <View style={{ flex: 1 }}>
          <Txt size={14} weight="bold" color={colors.white}>
            No connection
          </Txt>
          <Txt size={13} color={colors.onBlackMuted}>
            Check your data or Wi-Fi. We will keep trying.
          </Txt>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", left: 0, right: 0, alignItems: "center", paddingHorizontal: 16 },
  banner: {
    width: "100%",
    maxWidth: 560,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.black,
    borderRadius: 18,
    paddingVertical: 12,
    paddingLeft: 16,
    paddingRight: 16,
  },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.warning },
});
