import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Txt } from "@/components/Txt";
import { colors } from "@/theme";

/**
 * A strip at the top of every screen while the app cannot reach Finvest. What is on the
 * screen stays, and the app keeps trying by itself.
 */
export function OfflineBanner({ checking, onRetry }: { checking: boolean; onRetry: () => void }) {
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
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Try again now"
          disabled={checking}
          onPress={onRetry}
          style={({ pressed }) => [styles.retry, pressed && { opacity: 0.8 }]}
          testID="offline-retry"
        >
          {checking ? (
            <ActivityIndicator color={colors.ink} size="small" />
          ) : (
            <Txt size={13} weight="bold">
              Try again
            </Txt>
          )}
        </Pressable>
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
    paddingRight: 10,
  },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.warning },
  retry: {
    minWidth: 92,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.lime,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 14,
  },
});
